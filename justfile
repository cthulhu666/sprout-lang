set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
  @just --list

test:
  python3 -m unittest discover -s tests -v

parse file:
  python3 -m sprout.cli parse {{file}}

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
