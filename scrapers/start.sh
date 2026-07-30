#!/bin/bash

echo "🚀 Iniciando infraestructura scraper..."

# Limpiar locks de X y Chrome previos
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99 2>/dev/null || true
killall -9 google-chrome chrome chromium 2>/dev/null || true
find /app/data/chrome_profile -maxdepth 2 -type f \( -name "SingletonLock" -o -name "SingletonSocket" -o -name "SingletonCookie" \) -delete 2>/dev/null || true
rm -rf /app/data/chrome_profile/Default/Service\ Worker/ScriptCache 2>/dev/null || true
sleep 1

# Xvfb - display virtual
echo "🖥️  Iniciando Xvfb :99 ..."
Xvfb :99 -screen 0 1920x1080x24 &
sleep 2

# Window manager ligero
fluxbox &>/dev/null &
sleep 1

# VNC server
echo "📺 VNC en :5900 (password: ${VNC_PASSWORD:-scraper})"
x11vnc -display :99 -forever -shared -rfbport 5900 -passwd "${VNC_PASSWORD:-scraper}" &>/dev/null &
sleep 1

# Chrome con CDP
echo "🌐 Chrome CDP en :9223 ..."
DISPLAY=:99 google-chrome-stable \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --remote-debugging-port=9223 \
  --remote-debugging-address=0.0.0.0 \
  --user-data-dir=/app/data/chrome_profile \
  --no-first-run \
  --disable-default-apps \
  --mute-audio \
  --window-size=1920,1080 \
  about:blank &
sleep 3

# Proxy socat: 9222 → 9223 (expone CDP al exterior)
socat TCP-LISTEN:9222,bind=0.0.0.0,fork,reuseaddr TCP:127.0.0.1:9223 &

echo "✅ Listo."
echo "   VNC  → vnc://HOST:5900  (pass: ${VNC_PASSWORD:-scraper})"
echo "   CDP  → ws://HOST:9222"

# Mostrar la próxima ejecución programada de Holded
if [ -f /app/holded_invoice.py ]; then
  NEXT_RUN=$(python - <<'PY'
from datetime import datetime
import holded_invoice
next_run = holded_invoice.next_monthly_run()
print(next_run.strftime('%Y-%m-%d %H:%M'))
PY
)
  echo "⏱️  Próxima ejecución Holded: ${NEXT_RUN}"
fi

# Lanza el programador mensual de Holded en background
if [ -f /app/holded_invoice.py ]; then
  echo "📥 Iniciando descarga mensual de factura Holded..."
  python -u /app/holded_invoice.py >> /app/data/holded_invoice.log 2>&1 &
  echo "📥 Logs de Holded: /app/data/holded_invoice.log"
fi

# Lanza tu scraper en background
python /app/api.py &
API_PID=$!

# Mantener el contenedor corriendo y esperar a que termine algún proceso
wait $API_PID
