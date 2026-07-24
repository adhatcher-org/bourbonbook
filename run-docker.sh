#stop docker container
docker stop bourbonbook

#build new image
make build

#run new image
docker run -d \
  --name bourbonbook \
  --rm \
  --network bridge \
  -p 8000:8000 \
  --env DATA_DIR=/data \
  --volume "/Users/aaron/Documents/Development/bourbonbook/data:/data" \
  --env-file "/Users/aaron/Documents/Development/bourbonbook/data/.env" \
  bourbonbook:local-v1
