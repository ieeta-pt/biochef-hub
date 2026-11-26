
import pandas as pd
import matplotlib.pyplot as plt

CSV_FILE = "benchmark-results.csv"

df = pd.read_csv(CSV_FILE)
df["elapsed_ms"] = pd.to_numeric(df["elapsed_ms"])

# Extract dataset size and stdout mode from scenario.
df["dataset_size"] = df["scenario"].str.extract(r"(\d+MB)")
df["stdout_mode"] = df["scenario"].str.extract(r"-(file|discard|capture)$")

# Convert dataset size to numeric values for sorting.
df["size_mb"] = df["dataset_size"].str.replace("MB", "", regex=False).astype(int)

# Calculate average execution time.
averages = (
    df.groupby(["size_mb", "substrate", "stdout_mode"])["elapsed_ms"]
    .mean()
    .reset_index()
)

# Define ordering.
size_order = [1, 10, 100, 1000, 3200]
substrates = ["native", "nodejs", "browser_firefox", "browser_chrome"]
stdout_modes = ["file", "discard", "capture"]

# Color per substrate, line style per stdout mode.
colors = {
    "native": "tab:green",
    "nodejs": "gold",
    "browser_firefox": "tab:orange",
    "browser_chrome": "tab:red",
}

linestyles = {
    "capture": "-",
    "file": "--",
    "discard": ":",
}

fig, ax = plt.subplots(figsize=(12, 6))

for substrate in substrates:
    for mode in stdout_modes:
        subset = averages[
            (averages["substrate"] == substrate) &
            (averages["stdout_mode"] == mode)
        ].set_index("size_mb").reindex(size_order)

        ax.plot(
            size_order,
            subset["elapsed_ms"],
            color=colors[substrate],
            linestyle=linestyles[mode],
            marker="o",
            label=f"{substrate} - {mode}",
        )

ax.set_title("Native vs WASM Execution Time")
ax.set_xlabel("Dataset Size (MB)")
ax.set_ylabel("Execution Time (ms)")

ax.set_xticks(size_order)
ax.set_xticklabels([f"{size}MB" for size in size_order])

ax.legend(title="Substrate - stdout mode", bbox_to_anchor=(1.02, 1), loc="upper left")
ax.grid(True, which="both", linestyle="--", alpha=0.5)

# plt.yscale("log")
plt.xscale("log")
plt.tight_layout()
plt.show()
