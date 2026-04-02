set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
  @just --list

test:
  python3 scripts/run_parallel_tests.py

test-serial:
  python3 -m unittest discover -s tests -v

test-all:
  python3 -m unittest discover -s tests -v

test-parallel:
  python3 scripts/run_parallel_tests.py

test-integration:
  python3 -m unittest discover -s tests -p 'test_integration_io.py' -v

parse file:
  python3 -m sprout.cli parse {{file}}

fmt:
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 python3 -m sprout.cli fmt

fmt-check:
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 python3 -m sprout.cli fmt --check

fmt-file file:
  python3 -m sprout.cli fmt {{file}}

fmt-check-file file:
  python3 -m sprout.cli fmt --check {{file}}

lint:
  rg --files -0 -g '*.sprout' -g '*.spr' | xargs -0 -n 1 python3 -m sprout.cli lint

lint-file file:
  python3 -m sprout.cli lint {{file}}

check file:
  python3 -m sprout.cli check {{file}}

check-stdlib file:
  python3 -m sprout.cli check --with-stdlib {{file}}

run file:
  python3 -m sprout.cli run {{file}}

run-stdlib file:
  python3 -m sprout.cli run --with-stdlib {{file}}

compile file out:
  python3 -m sprout.cli compile {{file}} -o {{out}}

compile-native file out:
  python3 -m sprout.cli compile {{file}} --native -o {{out}}

compile-examples:
  for file in examples/*.sprout; do \
    flags=""; \
    if [ "$file" = "examples/result_demo.sprout" ]; then flags="--with-stdlib"; fi; \
    out="/tmp/$(basename "$file" .sprout).ll"; \
    python3 -m sprout.cli compile $flags "$file" -o "$out"; \
    echo "OK $file"; \
  done
