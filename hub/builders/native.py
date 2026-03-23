import subprocess
import os
import shutil

def build(tool_name, settings, source, output_dir="build"):
    buildsystem = settings['buildsystem']

    if buildsystem == "make":
        repo_url, tag, commit = source

        subprocess.run(["git", "clone", repo_url, tool_name], check=True)

        base_dir = os.getcwd()
        os.chdir(tool_name)

        if tag:
            subprocess.run(["git", "checkout", "tags/" + tag], check=True)
        elif commit:
            subprocess.run(["git", "checkout", commit], check=True)

        workdir = settings.get("workDir", ".")
        os.chdir(workdir)

        try:
            subprocess.run(f"make", shell=True, check=True)

            outputDir = settings.get('outputDir', '')
            from_dir = f"{base_dir}/{tool_name}/{outputDir}"
            dest_dir = f"{base_dir}/{output_dir}/{tool_name}"
            shutil.copytree(from_dir, dest_dir, dirs_exist_ok=True)
            
            return dest_dir
        except subprocess.CalledProcessError as e:
            print(f"Error building native binary: {e}")
            return None
        finally:
            # Remove the git repository
            shutil.rmtree(f"{base_dir}/{tool_name}")
            # Return to the correct dir
            os.chdir(base_dir)

    return ""