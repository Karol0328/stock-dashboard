#!/bin/bash
cd "$(dirname "$0")"
rm -f .git/index.lock
git add .
git -c user.email="bill19990328@gmail.com" -c user.name="Karol" commit -m "init: stock dashboard with Taiwan/US/Korea markets"
echo "Done! Removing this script..."
rm -- "$0"
