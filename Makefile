build:
	docker build -t ada/base:latest .

run:
	docker run -p 8888:8888 ada/base:latest