
import pandas as pd
import matplotlib.pyplot as plt

CSV_FILE = "benchmark-results.csv"

df = pd.read_csv(CSV_FILE)

df["elapsed_ms"] = pd.to_numeric(df["elapsed_ms"])

# Calculate average execution time for each scenario and substrate.
averages = (
    df.groupby(["scenario", "substrate"])["elapsed_ms"]
    .mean()
    .unstack()
)

# Ensure scenarios appear in dataset-size order.
scenario_order = ["1MB", "10MB", "100MB", "1000MB"]
averages = averages.reindex(scenario_order)

# Plot
ax = averages.plot(
    kind="line",
    marker="o",
    figsize=(10, 5),
)

plt.title("Native vs WASM Execution Time")
plt.xlabel("Dataset Size")
plt.ylabel("Execution Time (ms)")

plt.xticks(
    range(len(averages.index)),
    averages.index,
)

plt.legend(title="Substrate")
plt.grid(True, which="both", linestyle="--", alpha=0.5)

# plt.yscale("log")
plt.tight_layout()
plt.show()
