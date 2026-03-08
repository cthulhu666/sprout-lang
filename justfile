set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

default:
  @just --list

test:
  python3 -m unittest discover -s tests -v

parse file:
  python3 -m sprout.cli parse {{file}}

check file:
  python3 -m sprout.cli check {{file}}

run file:
  python3 -m sprout.cli run {{file}}
