HOST_DATA_DIR="${HOST_DATA_DIR:-$HOME/Development/bourbonbook/data}"

#stop docker container
docker stop bourbonbook

#build new image
make build

#run new image
docker run -d \
  --name bourbonbook \
  --rm \
  --network bridge \
  --add-host litellm:192.168.50.4 \
  -p 8000:8000 \
  --env DATA_DIR=/data \
  --volume "$HOST_DATA_DIR:/data" \
  --env-file "$HOST_DATA_DIR/.env" \
  bourbonbook:local-v1
