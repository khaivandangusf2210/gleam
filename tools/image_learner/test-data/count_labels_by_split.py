# Python
import pandas as pd
from sklearn.model_selection import train_test_split

csv_path = "80_20.csv"
df = pd.read_csv(csv_path)
label_col = "label"
split_col = "split"
validation_size = 0.1
random_state = 42

# Only split=0 and split=2 present: need to create validation set from split=0
idx_train = df.index[df[split_col] == 0].tolist()
idx_test = df.index[df[split_col] == 2].tolist()

# Stratified split for validation
stratify_arr = df.loc[idx_train, label_col]
train_idx, val_idx = train_test_split(
    idx_train,
    test_size=validation_size,
    random_state=random_state,
    stratify=stratify_arr,
)
df.loc[train_idx, split_col] = 0
df.loc[val_idx, split_col] = 1

split_map = {0: "Train", 1: "Validation", 2: "Test"}
for split_value, split_name in split_map.items():
    if split_value in df[split_col].values:
        counts = df[df[split_col] == split_value][label_col].value_counts().sort_index()
        print(f"{split_name} split label counts:")
        print(counts)
        print()
