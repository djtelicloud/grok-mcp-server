#!/bin/bash
# UniGrok MCP helper script

DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/logs/grok_mcp.pid"
CHILD_PID_FILE="$DIR/logs/grok_mcp_child.pid"
LOG_FILE="$DIR/logs/grok_mcp.log"

# HTTP gateway port (matches src/http_server.py default) and health probe URL
GROK_MCP_PORT="${PORT:-4765}"
HEALTH_URL="http://127.0.0.1:${GROK_MCP_PORT}/healthz"

mkdir -p "$DIR/logs"

# Locate uv
if command -v uv &> /dev/null; then
    UV_BIN="uv"
elif [ -f "$HOME/.local/bin/uv" ]; then
    UV_BIN="$HOME/.local/bin/uv"
else
    UV_BIN="uv"
fi

check_health() {
    if command -v curl &> /dev/null; then
        curl -fsS --max-time 2 "$HEALTH_URL" > /dev/null 2>&1
    else
        "$UV_BIN" --directory "$DIR" run python -c "import urllib.request; urllib.request.urlopen('$HEALTH_URL', timeout=2)" > /dev/null 2>&1
    fi
}

case "$1" in
    init)
        echo "Initializing UniGrok MCP workspace..."
        "$UV_BIN" --directory "$DIR" run python main.py init
        ;;

    start)
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "UniGrok MCP is already running (Runner PID: $(cat "$PID_FILE"))."
            exit 0
        fi
        
        if [ -z "$XAI_API_KEY" ] && [ ! -f "$DIR/.env" ]; then
            echo "Warning: XAI_API_KEY is not set and no .env file exists; the server will refuse to start."
        fi

        echo "Starting UniGrok MCP in the background (HTTP mode on port $GROK_MCP_PORT)..."
        # Run in a loop to keep alive/auto-restart on crash
        nohup sh -c '
            echo $$ > '"$PID_FILE"'
            trap "kill \$(cat '"$CHILD_PID_FILE"' 2>/dev/null) 2>/dev/null || true; rm -f '"$PID_FILE"' '"$CHILD_PID_FILE"'; exit 0" EXIT INT TERM
            while true; do
                '"$UV_BIN"' --directory '"$DIR"' run python main.py --http >> '"$LOG_FILE"' 2>&1 &
                CHILD_PID=$!
                echo $CHILD_PID > '"$CHILD_PID_FILE"'
                wait $CHILD_PID
                echo "$(date): UniGrok MCP server crashed/exited. Auto-restarting in 2s..." >> '"$LOG_FILE"'
                sleep 2
            done
        ' > /dev/null 2>&1 &

        # Wait for the health endpoint to answer (max ~20s)
        for _ in $(seq 1 20); do
            if check_health; then
                break
            fi
            sleep 1
        done
        RUNNER_PID=$(cat "$PID_FILE" 2>/dev/null || echo "$!")
        if check_health; then
            echo "UniGrok MCP started (Runner PID: $RUNNER_PID). Healthy at $HEALTH_URL. Logging to $LOG_FILE"
        else
            echo "UniGrok MCP launching (Runner PID: $RUNNER_PID) but $HEALTH_URL is not answering yet. Check $LOG_FILE"
        fi
        ;;
        
    stop)
        if [ ! -f "$PID_FILE" ]; then
            echo "UniGrok MCP is not running (no PID file found)."
        else
            RUNNER_PID=$(cat "$PID_FILE")
            echo "Stopping UniGrok MCP (Runner PID: $RUNNER_PID)..."
            # Kill the runner loop so it stops auto-restarting
            kill $RUNNER_PID 2>/dev/null || true
            
            # Kill child python process directly if we have the PID
            if [ -f "$CHILD_PID_FILE" ]; then
                CHILD_PID=$(cat "$CHILD_PID_FILE")
                echo "Terminating child process (PID: $CHILD_PID)..."
                kill $CHILD_PID 2>/dev/null || true
                rm -f "$CHILD_PID_FILE"
            fi
            
            rm -f "$PID_FILE"
            echo "Stopped."
        fi
        ;;
        
    status)
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "Status: RUNNING (Runner PID: $(cat "$PID_FILE"))"
            if [ -f "$CHILD_PID_FILE" ] && kill -0 $(cat "$CHILD_PID_FILE") 2>/dev/null; then
                echo "Child Process (PID: $(cat "$CHILD_PID_FILE")) is ACTIVE"
            else
                echo "Child Process is INACTIVE or starting"
            fi
            if check_health; then
                echo "Health: OK ($HEALTH_URL)"
            else
                echo "Health: NOT RESPONDING ($HEALTH_URL)"
            fi
            echo "Recent logs:"
            tail -n 10 "$LOG_FILE"
        else
            echo "Status: STOPPED"
        fi
        ;;
        
    models)
        echo "Querying available models..."
        "$UV_BIN" --directory "$DIR" run python -c "
import asyncio
from src.server import list_models
async def run():
    print(await list_models())
asyncio.run(run())
"
        ;;
        
    test)
        echo "Running self-test for all models..."
        "$UV_BIN" --directory "$DIR" run python -c "
import asyncio
from src.server import chat

async def test_model(model_name):
    print(f'Testing {model_name}...')
    try:
        res = await chat(prompt='Say the number 42 and nothing else', model=model_name)
        print(f'-> Response: {res.strip()}')
    except Exception as e:
        print(f'-> Failed: {e}')

async def main():
    models = ['grok-composer-2.5-fast', 'grok-build', 'grok-4.20-0309-reasoning', 'grok-4.3', 'grok-build-0.1']
    for m in models:
        await test_model(m)

asyncio.run(main())
"
        ;;
        
    *)
        echo "Usage: $0 {init|start|stop|status|models|test}"
        exit 1
        ;;
esac
