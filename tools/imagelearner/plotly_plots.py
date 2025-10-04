import json
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio
from sklearn.metrics import auc, roc_curve
from sklearn.preprocessing import label_binarize


def build_classification_plots(
    test_stats_path: str,
    training_stats_path: Optional[str] = None,
) -> List[Dict[str, str]]:
    """
    Read Ludwig’s test_statistics.json and build three interactive Plotly panels:
      - Confusion Matrix
      - ROC-AUC
      - Classification Report Heatmap

    Returns a list of dicts, each with:
      {
        "title": <plot title>,
        "html":  <HTML fragment for embedding>
      }
    """
    # --- Load test stats ---
    with open(test_stats_path, "r") as f:
        test_stats = json.load(f)
    label_stats = test_stats["label"]

    # common sizing
    cell = 40
    n_classes = len(label_stats["confusion_matrix"])
    side_px = max(cell * n_classes + 200, 600)
    common_cfg = {"displayModeBar": True, "scrollZoom": True}

    plots: List[Dict[str, str]] = []

    # 0) Confusion Matrix
    cm = np.array(label_stats["confusion_matrix"], dtype=int)
    # Try to get actual class names from per_class_stats keys (which contain the real labels)
    pcs = label_stats.get("per_class_stats", {})
    if pcs:
        labels = list(pcs.keys())
    else:
        labels = label_stats.get("labels", [str(i) for i in range(cm.shape[0])])
    total = cm.sum()

    fig_cm = go.Figure(
        go.Heatmap(
            z=cm,
            x=labels,
            y=labels,
            colorscale="Blues",
            showscale=True,
            colorbar=dict(title="Count"),
        )
    )
    fig_cm.update_traces(xgap=2, ygap=2)
    fig_cm.update_layout(
        title=dict(text="Confusion Matrix", x=0.5),
        xaxis_title="Predicted",
        yaxis_title="Observed",
        yaxis_autorange="reversed",
        width=side_px,
        height=side_px,
        margin=dict(t=100, l=80, r=80, b=80),
    )

    # annotate counts and percentages
    mval = cm.max() if cm.size else 0
    thresh = mval / 2
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            v = cm[i, j]
            pct = (v / total * 100) if total > 0 else 0
            color = "white" if v > thresh else "black"
            fig_cm.add_annotation(
                x=labels[j],
                y=labels[i],
                text=f"<b>{v}</b>",
                showarrow=False,
                font=dict(color=color, size=14),
                xanchor="center",
                yanchor="bottom",
                yshift=2,
            )
            fig_cm.add_annotation(
                x=labels[j],
                y=labels[i],
                text=f"{pct:.1f}%",
                showarrow=False,
                font=dict(color=color, size=13),
                xanchor="center",
                yanchor="top",
                yshift=-2,
            )

    plots.append({
        "title": "Confusion Matrix",
        "html": pio.to_html(
            fig_cm,
            full_html=False,
            include_plotlyjs="cdn",
            config=common_cfg
        )
    })

    # 1) ROC-AUC Curves (Multi-class)
    roc_plot = _build_roc_auc_plot(test_stats_path, labels, common_cfg)
    if roc_plot:
        plots.append(roc_plot)

    # 2) Classification Report Heatmap
    pcs = label_stats.get("per_class_stats", {})
    if pcs:
        classes = list(pcs.keys())
        metrics = ["precision", "recall", "f1_score"]
        z, txt = [], []
        for c in classes:
            row, trow = [], []
            for m in metrics:
                val = pcs[c].get(m, 0)
                row.append(val)
                trow.append(f"{val:.2f}")
            z.append(row)
            txt.append(trow)

        fig_cr = go.Figure(
            go.Heatmap(
                z=z,
                x=metrics,
                y=[str(c) for c in classes],
                text=txt,
                texttemplate="%{text}",
                colorscale="Reds",
                showscale=True,
                colorbar=dict(title="Value"),
            )
        )
        fig_cr.update_layout(
            title="Classification Report",
            xaxis_title="",
            yaxis_title="Class",
            width=side_px,
            height=side_px,
            margin=dict(t=80, l=80, r=80, b=80),
        )
        plots.append({
            "title": "Classification Report",
            "html": pio.to_html(
                fig_cr,
                full_html=False,
                include_plotlyjs=False,
                config=common_cfg
            )
        })

    return plots


def _build_roc_auc_plot(test_stats_path: str, class_labels: List[str], config: dict) -> Optional[Dict[str, str]]:
    """
    Build an interactive ROC-AUC curve plot for multi-class classification.
    Following sklearn's ROC example with micro-average and per-class curves.

    Args:
        test_stats_path: Path to test_statistics.json
        class_labels: List of class label names
        config: Plotly config dict

    Returns:
        Dict with title and HTML, or None if data unavailable
    """
    try:
        # Get the experiment directory from test_stats_path
        exp_dir = Path(test_stats_path).parent

        # Load predictions with probabilities
        predictions_path = exp_dir / "predictions.csv"
        if not predictions_path.exists():
            return None

        df_pred = pd.read_csv(predictions_path)

        # Extract probability columns (label_probabilities_0, label_probabilities_1, etc.)
        # or label_probabilities_<class_name> for string labels
        prob_cols = [col for col in df_pred.columns if col.startswith('label_probabilities_') and col != 'label_probabilities']

        # Sort by class number if numeric, otherwise keep alphabetical order
        if prob_cols and prob_cols[0].split('_')[-1].isdigit():
            prob_cols.sort(key=lambda x: int(x.split('_')[-1]))
        else:
            prob_cols.sort()  # Alphabetical sort for string class names

        if not prob_cols:
            return None

        # Get probabilities matrix (n_samples x n_classes)
        y_score = df_pred[prob_cols].values
        n_classes = len(prob_cols)

        # We need the true labels for the test set
        # Try to find the original dataset path from description.json
        desc_path = exp_dir / "description.json"
        if not desc_path.exists():
            return None

        with open(desc_path, 'r') as f:
            desc = json.load(f)

        dataset_path = desc.get('dataset', '')
        if not dataset_path or not Path(dataset_path).exists():
            return None

        # Load original dataset and filter for test set (split == 2)
        df_orig = pd.read_csv(dataset_path)
        df_test = df_orig[df_orig['split'] == 2].reset_index(drop=True)

        if len(df_test) != len(df_pred):
            # Fallback: assume test set is in the same order as predictions
            print(f"Warning: Test set size mismatch. Using first {len(df_pred)} test samples.")
            df_test = df_test.head(len(df_pred))

        y_true = df_test['label'].values

        # Get actual class names from probability column names
        actual_classes = [col.replace('label_probabilities_', '') for col in prob_cols]

        # Binarize the output following sklearn example
        # Use actual class names if they're strings, otherwise use range
        if isinstance(y_true[0], str):
            y_test = label_binarize(y_true, classes=actual_classes)
        else:
            y_test = label_binarize(y_true, classes=list(range(n_classes)))

        # Handle binary classification case
        if n_classes == 2:
            y_test = np.hstack([1 - y_test, y_test])

        # Compute ROC curve and ROC area for each class (following sklearn example)
        fpr = dict()
        tpr = dict()
        roc_auc = dict()

        for i in range(n_classes):
            if np.sum(y_test[:, i]) > 0:  # Check if class exists in test set
                fpr[i], tpr[i], _ = roc_curve(y_test[:, i], y_score[:, i])
                roc_auc[i] = auc(fpr[i], tpr[i])

        # Compute micro-average ROC curve and ROC area (sklearn example)
        fpr["micro"], tpr["micro"], _ = roc_curve(y_test.ravel(), y_score.ravel())
        roc_auc["micro"] = auc(fpr["micro"], tpr["micro"])

        # Create ROC curve plot
        fig_roc = go.Figure()

        # Colors for different classes
        colors = [
            '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',
            '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf'
        ]

        # Plot micro-average ROC curve first (most important)
        fig_roc.add_trace(go.Scatter(
            x=fpr["micro"],
            y=tpr["micro"],
            mode='lines',
            name=f'Micro-average ROC (AUC = {roc_auc["micro"]:.3f})',
            line=dict(color='deeppink', width=3, dash='dot'),
            hovertemplate=('<b>Micro-average ROC</b><br>'
                           'FPR: %{x:.3f}<br>'
                           'TPR: %{y:.3f}<br>'
                           f'AUC: {roc_auc["micro"]:.3f}<extra></extra>')
        ))

        # Plot ROC curve for each class
        for i in range(n_classes):
            if i in roc_auc:  # Only plot if class exists in test set
                # Use actual class names from probability columns
                class_name = actual_classes[i] if i < len(actual_classes) else (class_labels[i] if i < len(class_labels) else f"Class {i}")
                color = colors[i % len(colors)]

                fig_roc.add_trace(go.Scatter(
                    x=fpr[i],
                    y=tpr[i],
                    mode='lines',
                    name=f'{class_name} (AUC = {roc_auc[i]:.3f})',
                    line=dict(color=color, width=2),
                    hovertemplate=(f'<b>{class_name}</b><br>'
                                   'FPR: %{x:.3f}<br>'
                                   'TPR: %{y:.3f}<br>'
                                   f'AUC: {roc_auc[i]:.3f}<extra></extra>')
                ))

        # Add diagonal line (random classifier)
        fig_roc.add_trace(go.Scatter(
            x=[0, 1],
            y=[0, 1],
            mode='lines',
            name='Random Classifier',
            line=dict(color='gray', width=1, dash='dash'),
            hovertemplate='Random Classifier<br>AUC = 0.500<extra></extra>'
        ))

        # Calculate macro-average AUC
        class_aucs = [roc_auc[i] for i in range(n_classes) if i in roc_auc]
        if class_aucs:
            macro_auc = np.mean(class_aucs)
            title_text = f"ROC Curves (Micro-avg = {roc_auc['micro']:.3f}, Macro-avg = {macro_auc:.3f})"
        else:
            title_text = f"ROC Curves (Micro-avg = {roc_auc['micro']:.3f})"

        fig_roc.update_layout(
            title=dict(text=title_text, x=0.5),
            xaxis_title="False Positive Rate",
            yaxis_title="True Positive Rate",
            width=700,
            height=600,
            margin=dict(t=80, l=80, r=80, b=80),
            legend=dict(
                x=0.6,
                y=0.1,
                bgcolor="rgba(255,255,255,0.9)",
                bordercolor="rgba(0,0,0,0.2)",
                borderwidth=1
            ),
            hovermode='closest'
        )

        # Set equal aspect ratio and proper range
        fig_roc.update_xaxes(range=[0, 1.0])
        fig_roc.update_yaxes(range=[0, 1.05])

        return {
            "title": "ROC-AUC Curves",
            "html": pio.to_html(
                fig_roc,
                full_html=False,
                include_plotlyjs=False,
                config=config
            )
        }

    except Exception as e:
        print(f"Error building ROC-AUC plot: {e}")
        return None
