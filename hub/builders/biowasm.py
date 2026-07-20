from pathlib import Path
import docker
import os

IMAGE_NAME = "biochef-biowasm-builder"
bclient = docker.from_env()

def image_exists():
    try:
        bclient.images.get(IMAGE_NAME)
        return True
    except docker.errors.ImageNotFound:
        return False


def build_image(dockerfile_dir=".", dockerfile_name="Dockerfile"):
    print("Building Biowasm Docker image...")

    image, logs = bclient.images.build(
        path=dockerfile_dir,
        dockerfile=dockerfile_name,
        tag=IMAGE_NAME,
        rm=True
    )

    for chunk in logs:
        if "stream" in chunk:
            print(chunk["stream"], end="")

    return image


def build(tool_name, version, output_dir="build"):
    if not image_exists():
        build_image(dockerfile_name="biowasm.Dockerfile")

    output_dir = Path(output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    
    container = bclient.containers.run(
        image=IMAGE_NAME,
        user=f"{os.getuid()}:{os.getgid()}",
        working_dir="/biowasm",
        command=[
            "bash",
            "-c",
            (   
                f"python3 ./bin/compile.py "
                f"--tools {tool_name} "
                f"--versions {version} && "
                f"cp -r build/{tool_name}/{version} /output/{tool_name}"
            ),
        ],
        volumes={
            str(output_dir): {
                "bind": "/output",
                "mode": "rw",
            }
        },
        detach=True,
    )

    try:
        for line in container.logs(stream=True):
            print(line.decode(), end="")

        result = container.wait()

        if result["StatusCode"] != 0:
            print(f"Build failed with code {result['StatusCode']}")
            return ""

    finally:
        container.remove()


    return output_dir / tool_name
