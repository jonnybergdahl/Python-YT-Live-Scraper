#!/usr/bin/env zsh
setopt rm_star_silent

# Make docs
pandoc README.md -o README.rst
sphinx-build -b html docs/source docs/build
rm -rf ~/Developer/jonnybergdahl/jonnybergdahl.github.io/yt-live-scraper/*
cp -r docs/build/ ~/Developer/jonnybergdahl/jonnybergdahl.github.io/yt-live-scraper
touch ~/Developer/jonnybergdahl/jonnybergdahl.github.io/yt-live-scraper/.nojekyll

# Commit and push docs
pushd ~/Developer/jonnybergdahl/jonnybergdahl.github.io || exit
git checkout main
git pull
git add yt-live-scraper
if [ -n "$(git status --porcelain)" ]; then
  git commit -m "Update yt-live-scraper docs"
  git push
else
   echo "Branch main is up to date,nothing to do."
fi
popd || exit

# Make new version
rm -rf dist
python3 -m build
twine upload dist/*