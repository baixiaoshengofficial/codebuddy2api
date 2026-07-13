SHELL := /bin/bash

DEPLOY_HOST ?= 192.168.100.3
DEPLOY_USER ?= root
DEPLOY_DIR ?= /opt/codebuddy2api
DEPLOY_PORT ?= 8001
CODEBUDDY_SITE ?= china
INSTALL_DOCKER ?= 0

export DEPLOY_HOST
export DEPLOY_USER
export DEPLOY_DIR
export DEPLOY_PORT
export CODEBUDDY_SITE
export INSTALL_DOCKER
export OVERWRITE_ENV
export DEPLOY_PASSWORD
export CODEBUDDY_PASSWORD
export CMD

.PHONY: help frontend-dev frontend-build deploy deploy-install-docker deploy-cn deploy-intl ps logs restart down shell health check remote

help:
	@echo "CodeBuddy2API deployment targets"
	@echo ""
	@echo "  make deploy                  Deploy to $${DEPLOY_USER:-$(DEPLOY_USER)}@$${DEPLOY_HOST:-$(DEPLOY_HOST)}"
	@echo "  make deploy-install-docker   Deploy and install Docker if missing"
	@echo "  make deploy-cn               Deploy with CODEBUDDY_SITE=china"
	@echo "  make deploy-intl             Deploy with CODEBUDDY_SITE=international"
	@echo "  OVERWRITE_ENV=1 make deploy  Deploy and replace remote .env"
	@echo "  make ps                      Show remote compose status"
	@echo "  make logs                    Tail remote service logs"
	@echo "  make restart                 Restart remote service"
	@echo "  make down                    Stop remote service"
	@echo "  make shell                   Open SSH shell on remote host"
	@echo "  make health                  Check /health endpoint"
	@echo "  make frontend-dev            Start the Vue development server"
	@echo "  make frontend-build          Build the Vue admin console"
	@echo "  make check                   Build frontend and run local checks"
	@echo ""
	@echo "Examples:"
	@echo "  DEPLOY_PASSWORD='***' make deploy"
	@echo "  DEPLOY_PASSWORD='***' make deploy-cn"
	@echo "  OVERWRITE_ENV=1 make deploy-cn"

deploy:
	@./scripts/deploy_docker.sh

deploy-install-docker:
	@INSTALL_DOCKER=1 ./scripts/deploy_docker.sh

deploy-cn:
	@CODEBUDDY_SITE=china ./scripts/deploy_docker.sh

deploy-intl:
	@CODEBUDDY_SITE=international ./scripts/deploy_docker.sh

ps:
	@$(MAKE) --no-print-directory remote CMD="cd '$(DEPLOY_DIR)' && (docker compose ps || docker-compose ps)"

logs:
	@$(MAKE) --no-print-directory remote CMD="cd '$(DEPLOY_DIR)' && (docker compose logs -f --tail=200 || docker-compose logs -f --tail=200)"

restart:
	@$(MAKE) --no-print-directory remote CMD="cd '$(DEPLOY_DIR)' && (docker compose restart || docker-compose restart)"

down:
	@$(MAKE) --no-print-directory remote CMD="cd '$(DEPLOY_DIR)' && (docker compose down || docker-compose down)"

shell:
	@if [[ -n "$$DEPLOY_PASSWORD" ]]; then \
		expect -c 'set timeout -1; spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$$env(DEPLOY_USER)@$$env(DEPLOY_HOST)"; expect { -re "(?i)password:" { send "$$env(DEPLOY_PASSWORD)\r"; exp_continue } eof }'; \
	else \
		ssh -o StrictHostKeyChecking=accept-new "$$DEPLOY_USER@$$DEPLOY_HOST"; \
	fi

health:
	@curl -fsS "http://$(DEPLOY_HOST):$(DEPLOY_PORT)/health" && echo

frontend-dev:
	@cd frontend && npm run dev

frontend-build:
	@cd frontend && npm run build

check:
	@bash -n scripts/deploy_docker.sh
	@cd frontend && npm run build
	@python3 -m py_compile config.py web.py src/*.py
	@git diff --check

remote:
	@if [[ -z "$$CMD" ]]; then echo "CMD is required"; exit 2; fi; \
	if [[ -n "$$DEPLOY_PASSWORD" ]]; then \
		expect -c 'set timeout -1; spawn ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "$$env(DEPLOY_USER)@$$env(DEPLOY_HOST)" "$$env(CMD)"; expect { -re "(?i)password:" { send "$$env(DEPLOY_PASSWORD)\r"; exp_continue } eof }; catch wait result; exit [lindex $$result 3]'; \
	else \
		ssh -o StrictHostKeyChecking=accept-new "$$DEPLOY_USER@$$DEPLOY_HOST" "$$CMD"; \
	fi
