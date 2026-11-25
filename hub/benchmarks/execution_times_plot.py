import pandas as pd
import matplotlib.pyplot as plt

substrate_colors = {
    "native": "blue",
    "nodejs": "green",
    "browser_firefox": "orange",
    "browser_chrome": "red",
}

stdout_styles = {
    "capture": "-",
    "discard": "--",
    "file": ":",
}

CSV_FILE = "benchmark-results.csv"

df = pd.read_csv(CSV_FILE)

df["elapsed_ms"] = pd.to_numeric(df["elapsed_ms"])
df["file_size_bytes"] = pd.to_numeric(df["file_size_bytes"])

df["runtime"] = df["substrate"]
# df["runtime"] = df["substrate"] + " (" + df["stdout_mode"] + ")"

averages = (
    df.groupby(["file", "file_size_bytes", "runtime"])["elapsed_ms"]
    .mean()
    .unstack()
)

averages = averages.sort_index(level="file_size_bytes")

x_labels = [
    f"{size / (1024 ** 2):.1f} MiB"
    for size in averages.index.get_level_values("file_size_bytes")
]

averages.plot(
    kind="line",
    marker="o",
    figsize=(10, 5)
)

# ax = averages.plot(
#     kind="line",
#     marker="o",
#     figsize=(10, 5),
#     color=[
#         substrate_colors[col.split(" (")[0]]
#         for col in averages.columns
#     ],
#     style=[
#         stdout_styles[col.split("(")[1].rstrip(")")]
#         for col in averages.columns
#     ],
# )

plt.title("Native vs WASM Execution Time")
plt.xlabel("Dataset Size")
plt.ylabel("Execution Time (ms)")

# plt.yscale("log")

plt.xticks(
    range(len(averages.index)),
    x_labels,
    rotation=45,
    ha="right"
)

plt.legend(title="Substrate")
plt.grid(True, which="both", linestyle="--", alpha=0.5)
plt.tight_layout()

plt.show()