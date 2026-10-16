# Agentes-fin

Sistema de agentes que vende primas de opciones (plazo de 7 a 21 días, strike vendido con delta 0.10 a 0.20). Primero se prueba en la cuenta **paper** de Alpaca y después se migra a tastytrade.

## Estructura

| Carpeta / archivo | Qué hay |
|---|---|
| `broker/` | La única pieza que habla con el bróker. `base.py` define la interfaz; `alpaca_paper.py` es el adaptador de Alpaca paper. Para migrar a tastytrade se agrega otro adaptador acá y no se toca nada más. |
| `datos/` | Datos de mercado para los agentes (por ahora, la cadena de opciones en la ventana de 7 a 21 días). |
| `agentes/` | Los nueve agentes (research, consejo y jefe). Desde la fase 2. |
| `bitacora/` | Registro de trades. |
| `orquestador.py` | Punto de entrada. |
| `tests/` | Pruebas sin red. |

## Uso (fase 1, solo lectura)

Las claves se leen de las variables de entorno `ALPACA_API_KEY` y `ALPACA_SECRET_KEY`. Nunca van en el código.

```bash
pip install -r requirements.txt
python orquestador.py estado                 # cuenta paper + cadena de SPY de 7 a 21 días, |delta| 0.10 a 0.20
python orquestador.py estado --todo          # toda la cadena
python orquestador.py estado --simbolo QQQ --delta-min 0.05 --delta-max 0.30
python orquestador.py cuenta                 # solo la cuenta
python -m unittest discover -s tests -t .    # pruebas
```

Por defecto los precios de opciones vienen del feed `indicative` de Alpaca (gratis, con algo de demora). Con suscripción se puede usar `--feed opra`.

## Seguridad

- El adaptador apunta siempre a `paper-api.alpaca.markets`; la URL no se puede cambiar a la de cuentas reales.
- Antes de pedir cualquier dato verifica que el número de cuenta sea de paper (empieza con `PA`). Si no lo es, se detiene con `ALTO` y no hace nada más.
- En la fase 1 no existe ningún método para enviar órdenes: el adaptador solo hace pedidos GET.
