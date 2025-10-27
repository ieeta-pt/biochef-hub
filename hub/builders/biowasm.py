import subprocess
import os
import shutil

def build(tool_name, version, output_dir="build", biowasm_dir="biowasm", biowasm_repo="https://github.com/biowasm/biowasm"):
    if not os.path.isdir(biowasm_dir):
        subprocess.run(["git", "clone", biowasm_repo, biowasm_dir], check=True)
    
    base_dir = os.getcwd()
    os.chdir(biowasm_dir)
    subprocess.run(["python", "./bin/compile.py", "--tools", tool_name])
    os.chdir(base_dir)

    if os.path.isdir(f"{biowasm_dir}/build"):
        shutil.copytree(f"{biowasm_dir}/build/{tool_name}/{version}", f"{output_dir}/{tool_name}", dirs_exist_ok=True)
        shutil.rmtree(f"{biowasm_dir}/build")
        return os.path.abspath(f"{output_dir}/{tool_name}")
    
    return None