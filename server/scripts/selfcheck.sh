#!/bin/sh
set -eu
python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test
BANK_SYNC_DISABLE=1 python manage.py process_recurring
printf '\nSelf-check passed.\n'
