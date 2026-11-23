#!/usr/bin/env bash
set -euo pipefail

sha=${1:?Expected the tested commit SHA}
source_dir=${2:?Expected the extracted source directory}
[[ "$sha" =~ ^[0-9a-f]{40}$ ]]
[[ -f "$source_dir/Dockerfile" && -f "$source_dir/.dockerignore" ]]

cd /root/workout-bot
umask 077
mkdir -p .deploy
exec 9>.deploy/deploy.lock
flock -n 9

compose=(docker compose -f docker-compose.yml)
image="workout-bot-new:${sha:0:7}"
rollback_image=workout-bot-new:rollback

if existing_revision=$(docker image inspect --format '{{index .Config.Labels "org.opencontainers.image.revision"}}' "$image" 2>/dev/null); then
  if [[ "$existing_revision" != "$sha" ]]; then
    printf 'Image tag %s is already assigned to a different or unknown revision.\n' "$image" >&2
    exit 1
  fi
fi

docker build --label "org.opencontainers.image.revision=$sha" -t "$image" "$source_dir"
image_id=$(docker image inspect --format '{{.Id}}' "$image")
candidate_head=$(docker run --rm --network none --entrypoint python "$image" -m alembic heads)
database_revision=$("${compose[@]}" exec -T db psql -X -v ON_ERROR_STOP=1 \
  -U bot_user -d workout_bot -Atc 'SELECT version_num FROM public.alembic_version')
if [[ "$candidate_head" != "$database_revision (head)" ]]; then
  printf 'Database revision is %s, candidate expects %s. Apply the approved migration separately.\n' \
    "$database_revision" "$candidate_head" >&2
  exit 1
fi

running_container=$("${compose[@]}" ps -q bot)
previous_id=$(docker inspect --format '{{.Image}}' "${running_container:?No running bot to preserve}")
if [[ "$image_id" == "$previous_id" ]]; then
  echo "Bot already runs $sha."
  exit 0
fi

docker image tag "$previous_id" "$rollback_image"
candidate_override=$(mktemp .deploy/candidate.XXXXXXXX)
trap 'rm -f -- "$candidate_override"' EXIT
printf 'services:\n  bot:\n    image: %s\n' "$image" > "$candidate_override"
printf 'services:\n  bot:\n    image: %s\n' "$rollback_image" > .deploy/compose.rollback.yml

up_args=(up -d --no-deps --no-build --pull never --wait --wait-timeout 90 bot)
if "${compose[@]}" -f "$candidate_override" "${up_args[@]}" &&
   [[ "$(docker inspect --format '{{.Image}}' "$("${compose[@]}" ps -q bot)")" == "$image_id" ]] &&
   mv -- "$candidate_override" .deploy/compose.release.yml; then
  printf 'Deployed %s with database revision %s.\n' "$sha" "$database_revision"
else
  echo 'Switch to the new image failed. Restoring the previous image.' >&2
  "${compose[@]}" -f .deploy/compose.rollback.yml "${up_args[@]}"
  cp -- .deploy/compose.rollback.yml .deploy/compose.release.yml
  exit 1
fi
