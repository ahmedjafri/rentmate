#!/bin/bash

VENV_PYTHON="$HOME/.cache/pypoetry/virtualenvs/rentmate-UFMNINIZ-py3.12/bin/python"

case "$1" in
start)
$VENV_PYTHON main.py --port 8000 --log-level debug --reload
;;
dev)
RENTMATE_ENV=development $VENV_PYTHON main.py --port 8002 --log-level debug --reload # --ssl-keyfile=key.key --ssl-certfile=cert.crt
;;
cli)
$VENV_PYTHON db/cli.py
;;
build-db)
echo "Generating models..."
$VENV_PYTHON db/generate_models.py db/schema.graphql db/models.py
;;
deploy-dev)
echo "Building..."
eval $(minikube docker-env)
docker build -t rentmate:latest .
kubectl rollout restart deployment/api-server
eval $(minikube docker-env -u)
;;
test)
$VENV_PYTHON -m pytest "${@:2}"
;;
port-forward)
echo "Port forwarding..."
sudo iptables -t nat -A PREROUTING -p tcp --dport 30001 -j DNAT --to-destination $(minikube ip):30001
sudo iptables -A FORWARD -p tcp -d $(minikube ip) --dport 30001 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT
;;
lint)
echo "Running ruff..."
poetry run ruff check .
echo ""
echo "Running keyword-only params + private import checker..."
poetry run python scripts/lint_kwargs.py
;;
*)
echo "Invalid argument: $1"
usage
;;
esac