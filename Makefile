up:
	docker-compose up -d

 down:
	docker-compose down

logs:
	docker-compose logs -f

migrate:
	alembic upgrade head

build:
	docker-compose build

restart:
	docker-compose down
	docker-compose up -d

ps:
	docker-compose ps
