"""
smart_engine.py
-----------------
TitonSmartEngine: motor de calificación DINÁMICO y PROPORCIONAL al tamaño
real de la empresa, en vez de usar filtros fijos universales (ej. "float
menor a 10M para cualquier empresa", que trata igual a una compañía de
$15M de capitalización que a una de $300M).

Filosofía (resumen del razonamiento, ver la respuesta en el chat para el
detalle completo):
  - El float debe medirse en DÓLARES (float_shares × precio), no solo en
    número de acciones — así se normaliza automáticamente por precio.
  - El Float Turnover Ratio (float / acciones totales) mide qué tan
    "controlada" está la empresa por insiders — más control = más riesgo
    de manipulación/dilución sorpresiva.
  - El RVOL Estructural (volumen premarket / float) mide cuántas veces se
    "recicló" el float completo antes de la campana — es la versión
    cuantificada del concepto de "reciclaje de acciones" de los traders
    profesionales de small caps.

Esta clase es un motor PURO: recibe un diccionario con los datos y
devuelve el veredicto. No hace llamadas de red — así se puede probar de
forma aislada y reutilizar tanto en el bot en vivo como en el backtest.
"""

from dataclasses import dataclass


@dataclass
class TitonSmartEngineConfig:
    # --- Filtros duros (luz roja = rechazo automático, score 0.0) ---
    price_min: float = 1.00
    gap_min_pct: float = 10.0

    # --- Market Cap: banda óptima de micro/small cap ---
    market_cap_optimal_min: float = 15_000_000
    market_cap_optimal_max: float = 150_000_000
    # fuera de la banda óptima, el score decae hasta llegar a 0 en este múltiplo
    market_cap_decay_multiple: float = 3.0

    # --- Float Turnover Ratio: qué % de las acciones totales es float público ---
    float_turnover_full_score_min: float = 0.6   # >= 60% del total es float -> score completo
    float_turnover_zero_score_at: float = 0.10    # <= 10% del total es float -> score 0 (alto riesgo insider)

    # --- RVOL estructural (volumen premarket / float) = "reciclaje" ---
    structural_rvol_full_score_at: float = 0.50   # 50%+ del float ya rotó en premarket -> score completo

    # --- Umbral de aprobación ---
    approval_threshold: float = 9.0

    # --- Pesos de cada componente (deben sumar 10.0) ---
    weight_market_cap: float = 2.0
    weight_float_turnover: float = 1.0
    weight_catalyst_gap: float = 3.0
    weight_structural_rvol: float = 2.5
    weight_price_structure: float = 1.5


class TitonSmartEngine:
    """
    Uso:
        engine = TitonSmartEngine()
        resultado = engine.evaluate({
            "ticker": "IMRN",
            "current_price": 1.80,
            "prev_close": 1.10,
            "market_cap": 45_000_000,
            "float_shares": 8_000_000,
            "shares_outstanding": 20_000_000,
            "premarket_volume": 3_500_000,
            "catalyst_verified": True,
            "sec_dilution_blocked": False,
        })
        # resultado["score"], resultado["signal"], resultado["breakdown"], resultado["reasons"]
    """

    def __init__(self, cfg: TitonSmartEngineConfig = None):
        self.cfg = cfg or TitonSmartEngineConfig()

    # ------------------------------------------------------------------
    # COMPONENTES DE SCORING (cada uno documentado con su lógica financiera)
    # ------------------------------------------------------------------
    def _score_market_cap_band(self, market_cap: float) -> float:
        """
        Las empresas de $15M-$150M de capitalización son lo suficientemente
        pequeñas para moverse con fuerza ante un catalizador, pero no tan
        microscópicas que un solo comprador institucional pueda manipular
        el precio a su antojo. Fuera de esa banda, el score decae
        linealmente hasta 0 al llegar a `market_cap_decay_multiple` veces
        el límite de la banda.
        """
        if not market_cap or market_cap <= 0:
            return 0.0

        lo, hi = self.cfg.market_cap_optimal_min, self.cfg.market_cap_optimal_max
        if lo <= market_cap <= hi:
            return self.cfg.weight_market_cap

        if market_cap < lo:
            floor = lo / self.cfg.market_cap_decay_multiple
            if market_cap <= floor:
                return 0.0
            fraction = (market_cap - floor) / (lo - floor)
        else:
            ceiling = hi * self.cfg.market_cap_decay_multiple
            if market_cap >= ceiling:
                return 0.0
            fraction = (ceiling - market_cap) / (ceiling - hi)

        return round(self.cfg.weight_market_cap * max(0.0, min(1.0, fraction)), 3)

    def _score_float_turnover_ratio(self, float_shares: float, shares_outstanding: float) -> float:
        """
        Float Turnover Ratio = float / acciones totales en circulación.

        Un ratio ALTO (ej. 0.8) significa que casi todas las acciones de
        la empresa están libres para negociarse — pocos insiders
        controlando el suministro, comportamiento más "de mercado libre"
        y menos sujeto a que un insider "abra el grifo" de golpe.

        Un ratio BAJO (ej. 0.15) significa que la mayoría de las acciones
        están en manos de insiders/fondos bloqueados — el pequeño float
        libre se mueve muy fuerte con poco volumen, pero también es más
        fácil de manipular y más propenso a un "unlock" sorpresivo que
        diluya de golpe.
        """
        if not float_shares or not shares_outstanding or shares_outstanding <= 0:
            return 0.0  # sin datos suficientes, no se otorgan ni se quitan puntos aquí

        ratio = min(float_shares / shares_outstanding, 1.0)

        full_at = self.cfg.float_turnover_full_score_min
        zero_at = self.cfg.float_turnover_zero_score_at

        if ratio >= full_at:
            return self.cfg.weight_float_turnover
        if ratio <= zero_at:
            return 0.0

        fraction = (ratio - zero_at) / (full_at - zero_at)
        return round(self.cfg.weight_float_turnover * fraction, 3)

    def _score_catalyst_and_gap(self, gap_pct: float, catalyst_verified) -> float:
        """
        Combina la fuerza del gap (movimiento % frente al cierre anterior)
        con la confirmación de que existe un catalizador real detrás
        (noticia, contrato, aprobación) — un gap grande SIN catalizador
        suele ser ruido o manipulación de corto plazo, mientras que un
        catalizador real sostiene el movimiento durante más tiempo.
        """
        gap_component_max = self.cfg.weight_catalyst_gap * (2 / 3)   # 2/3 del peso total al gap
        catalyst_component_max = self.cfg.weight_catalyst_gap * (1 / 3)  # 1/3 al catalizador

        gap_score = 0.0
        if gap_pct and gap_pct >= self.cfg.gap_min_pct:
            # Escala suave: en el mínimo -> mitad de puntos, al doble del mínimo -> puntos completos
            fraction = min(1.0, 0.5 + 0.5 * ((gap_pct - self.cfg.gap_min_pct) / self.cfg.gap_min_pct))
            gap_score = gap_component_max * fraction

        catalyst_score = catalyst_component_max if catalyst_verified else 0.0

        return round(gap_score + catalyst_score, 3)

    def _score_structural_rvol(self, session_volume: float, float_shares: float) -> float:
        """
        RVOL Estructural = volumen acumulado EN LA SESIÓN ACTUAL / float total.

        Esto es el "reciclaje de acciones" cuantificado: si el volumen
        negociado en la sesión ya iguala o supera una fracción importante
        del float completo, significa que las mismas acciones se están
        revendiendo varias veces entre traders — la señal más confiable
        de que hay interés genuino y suficiente liquidez para sostener un
        movimiento explosivo.

        Generalizado a cualquier sesión (premarket, regular, after-hours):
        el float de una empresa no cambia según la hora del día, así que
        la misma fórmula es válida sin importar qué volumen se le pase —
        solo hay que pasarle el volumen correcto de la sesión vigente
        (ver `session_volume` en evaluate()).
        """
        if not session_volume or not float_shares or float_shares <= 0:
            return 0.0

        ratio = session_volume / float_shares
        fraction = min(1.0, ratio / self.cfg.structural_rvol_full_score_at)
        return round(self.cfg.weight_structural_rvol * fraction, 3)

    def _score_price_structure(self, price: float) -> float:
        """
        Zona de precio "sana" para small caps de momentum: ni tan barata
        que sea terreno fácil para manipulación de centavos, ni tan cara
        que el tamaño de posición se vuelva impráctico para cuentas
        pequeñas. Puntaje completo entre $2 y $15, decae hacia los bordes.
        """
        if not price or price < self.cfg.price_min:
            return 0.0
        if 2.0 <= price <= 15.0:
            return self.cfg.weight_price_structure
        if price < 2.0:
            fraction = (price - self.cfg.price_min) / (2.0 - self.cfg.price_min)
        else:
            fraction = max(0.0, 1.0 - (price - 15.0) / 15.0)
        return round(self.cfg.weight_price_structure * max(0.0, min(1.0, fraction)), 3)

    # ------------------------------------------------------------------
    # EVALUACIÓN COMPLETA
    # ------------------------------------------------------------------
    def evaluate(self, data: dict) -> dict:
        ticker = data.get("ticker", "?")
        price = data.get("current_price")
        prev_close = data.get("prev_close")
        market_cap = data.get("market_cap")
        float_shares = data.get("float_shares")
        shares_outstanding = data.get("shares_outstanding")
        # "session_volume" es la clave nueva y genérica (premarket/regular/after-hours,
        # según la sesión vigente). Se acepta "premarket_volume" como alias por
        # compatibilidad con backtest.py y cualquier llamador anterior.
        session_volume = data.get("session_volume", data.get("premarket_volume"))
        catalyst_verified = data.get("catalyst_verified")
        sec_dilution_blocked = data.get("sec_dilution_blocked", False)

        gap_pct = None
        if price and prev_close and prev_close > 0:
            gap_pct = ((price - prev_close) / prev_close) * 100

        result = {
            "ticker": ticker, "score": 0.0, "signal": "RECHAZADO",
            "breakdown": {}, "reasons": [], "gap_pct": gap_pct,
            "float_market_cap": (float_shares * price) if (float_shares and price) else None,
        }

        # --- LUCES ROJAS: rechazo automático, score 0.0 ---
        if not price or price < self.cfg.price_min:
            result["reasons"].append(f"Precio por debajo del piso mínimo (${self.cfg.price_min})")
            return result
        if sec_dilution_blocked:
            result["reasons"].append("Bloqueado por SEC Shield: oferta de dilución activa detectada")
            return result
        if gap_pct is None or gap_pct < self.cfg.gap_min_pct:
            result["reasons"].append(
                f"Gap insuficiente ({gap_pct if gap_pct is not None else 'N/D'}% < {self.cfg.gap_min_pct}%)"
            )
            return result

        # --- LUZ VERDE: calcular el score ponderado ---
        breakdown = {
            "market_cap_band": self._score_market_cap_band(market_cap),
            "float_turnover_ratio": self._score_float_turnover_ratio(float_shares, shares_outstanding),
            "catalyst_and_gap": self._score_catalyst_and_gap(gap_pct, catalyst_verified),
            "structural_rvol": self._score_structural_rvol(session_volume, float_shares),
            "price_structure": self._score_price_structure(price),
        }
        total_score = round(sum(breakdown.values()), 2)

        result["breakdown"] = breakdown
        result["score"] = total_score
        result["signal"] = (
            "Single-Bullet Aprobado" if total_score >= self.cfg.approval_threshold else "EN OBSERVACIÓN"
        )
        if total_score < self.cfg.approval_threshold:
            result["reasons"].append(
                f"Score {total_score}/10 por debajo del umbral de aprobación ({self.cfg.approval_threshold})"
            )

        return result
