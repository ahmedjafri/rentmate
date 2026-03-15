#!/bin/bash

case "$1" in
start)
poetry run python serve.py --port 8000 --log-level debug --reload
;;
dev)
RENTMATE_ENV=development poetry run python serve.py --port 8002 --log-level debug --reload # --ssl-keyfile=key.key --ssl-certfile=cert.crt
;;
cli)
poetry run python db/cli.py
;;
build-db)
echo "Generating models..."
poetry run python db/generate_models.py db/schema.graphql db/models.py
;;
deploy-dev)
echo "Building..."
eval $(minikube docker-env)
docker build -t rentmate:latest .
kubectl rollout restart deployment/api-server
eval $(minikube docker-env -u)
;;
test)
poetry run pytest "${@:2}"
;;
port-forward)
echo "Port forwarding..."
sudo iptables -t nat -A PREROUTING -p tcp --dport 30001 -j DNAT --to-destination $(minikube ip):30001
sudo iptables -A FORWARD -p tcp -d $(minikube ip) --dport 30001 -m state --state NEW,RELATED,ESTABLISHED -j ACCEPT
;;
*)
echo "Invalid argument: $1"
usage
;;
esac