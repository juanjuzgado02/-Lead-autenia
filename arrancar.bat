@echo off
rem ===========================================================================
rem  Autenia — arrancar el bot
rem ===========================================================================
rem  Doble clic aqui. Escribe el guion del dia, te lo manda a Telegram y se
rem  queda escuchando los botones hasta que cierres esta ventana.
rem
rem  El bot solo vive mientras esta ventana este abierta y el ordenador
rem  encendido: pregunta a Telegram cada pocos segundos, asi que suspendido no
rem  vale. Lo que pulses con el bot apagado no se pierde — Telegram lo guarda
rem  24 horas — pero no pasa nada hasta que lo vuelvas a arrancar.
rem ===========================================================================

cd /d "%~dp0"

echo.
echo   AUTENIA — arrancando
echo   ----------------------------------------------------------------
echo   Ctrl+C o cerrar esta ventana para parar.
echo.

python autenia_bot.py both

echo.
echo   ----------------------------------------------------------------
echo   El bot se ha parado. Telegram no recibira ordenes hasta que lo
echo   vuelvas a arrancar.
echo.
pause
