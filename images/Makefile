pull:
	docker pull krande/ada:base

build:
	docker krande/ada:base cp tests/test_code_aster_fem.py krande/ada:/

test:
	docker container cp tests/test_code_aster_fem.py krande/ada:/ && docker exec -it krande/ada:base bash -c "pytest"

test-doc:
	docker run --mount type=bind,source="$(pwd)/tmp",target=/home/tests/fem/temp ada/testing bash -c "pip install pytest pandas pydantic && cd /home/tests/fem && pytest && python build_verification_report.py"