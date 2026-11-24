import sys
from pathlib import Path
import yaml

print(sys.argv)

if (len(sys.argv) != 2):
    print("usage: python get_recipe_info.py <recipe_dir>")
    exit(-1)

total_tools = 0
total_operations = 0

recipes_dir = Path(sys.argv[1])
for recipe_file in recipes_dir.rglob("biochef.yaml"):
    with recipe_file.open("r", encoding="utf-8") as f:
        lines = f.readlines()

    line_count = len(lines)

    recipe = yaml.safe_load("".join(lines)) or {}

    name = recipe.get("name")
    n_operations = len(recipe.get("operations"))
    total_operations += n_operations
    
    strategy_list = list(recipe.get("build").keys())
    substrates = "/".join("WASM" if s == "wasm" else "Native" for s in strategy_list)
    
    wasm_strategy = recipe.get("build").get("wasm").get("strategy")
    
    output = f"{name} & {line_count} & {n_operations} & {substrates} & {wasm_strategy} \\\\"
    print(output)

print("number of operations:", total_operations)

