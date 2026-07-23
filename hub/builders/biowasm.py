from pathlib import Path
import docker
import os
import tarfile
import io

IMAGE_NAME = "biochef-biowasm-builder"
bclient = docker.from_env()
BUILDERS_DIR = Path(__file__).resolve().parent

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


def copy_from_container(container, source_path, destination):
    """
    Copies the contents of the folder at source_path 
    from inside the container to the destination
    """
    
    buffer = io.BytesIO()
    
    stream, _ = container.get_archive(source_path)
    for chunk in stream:
        buffer.write(chunk)

    buffer.seek(0)

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with tarfile.open(fileobj=buffer) as tar:
        members = tar.getmembers()

        for member in members:
            # Remove the top-level directory so only the contents are extracted
            path_parts = Path(member.name).parts
            member.name = str(Path(*path_parts[1:]))

        tar.extractall(destination, members)

def build(tool_name, version, output_dir="build"):
    if not image_exists():
        build_image(str(BUILDERS_DIR), dockerfile_name="biowasm.Dockerfile")

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
                f"--versions {version}"
            ),
        ],
        detach=True,
    )

    try:
        for line in container.logs(stream=True):
            print(line.decode(), end="")

        result = container.wait()

        if result["StatusCode"] != 0:
            print(f"Build failed with code {result['StatusCode']}")
            return ""

        copy_from_container(
            container,
            f"/biowasm/build/{tool_name}/{version}",
            output_dir / tool_name,
        )
        
    finally:
        container.remove()

    return output_dir / tool_name
