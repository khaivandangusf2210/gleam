import base64
import logging
import os

import matplotlib.pyplot as plt
import pandas as pd
import shap
from pycaret.classification import ClassificationExperiment
from pycaret.regression import RegressionExperiment

logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger(__name__)


class FeatureImportanceAnalyzer:
    def __init__(
        self,
        task_type,
        output_dir,
        data_path=None,
        data=None,
        target_col=None,
        exp=None,
        best_model=None,
    ):
        self.task_type = task_type
        self.output_dir = output_dir
        self.exp = exp
        self.best_model = best_model

        if exp is not None:
            # Assume all configs (data, target) are in exp
            self.data = exp.dataset.copy()
            self.target = exp.target_param
            LOG.info("Using provided experiment object")
        else:
            if data is not None:
                self.data = data
                LOG.info("Data loaded from memory")
            else:
                self.target_col = target_col
                self.data = pd.read_csv(data_path, sep=None, engine="python")
                self.data.columns = self.data.columns.str.replace(".", "_")
                self.data = self.data.fillna(self.data.median(numeric_only=True))
            self.target = self.data.columns[int(target_col) - 1]
            self.exp = (
                ClassificationExperiment()
                if task_type == "classification"
                else RegressionExperiment()
            )

        self.plots = {}

    def setup_pycaret(self):
        if self.exp is not None and hasattr(self.exp, "is_setup") and self.exp.is_setup:
            LOG.info("Experiment already set up. Skipping PyCaret setup.")
            return
        LOG.info("Initializing PyCaret")
        setup_params = {
            "target": self.target,
            "session_id": 123,
            "html": True,
            "log_experiment": False,
            "system_log": False,
        }
        self.exp.setup(self.data, **setup_params)

    def save_tree_importance(self):
        model = self.best_model or self.exp.get_config("best_model")
        processed_features = self.exp.get_config("X_transformed").columns

        importances = None
        model_type = model.__class__.__name__
        self.tree_model_name = model_type

        if hasattr(model, "feature_importances_"):
            importances = model.feature_importances_
        elif hasattr(model, "coef_"):
            importances = abs(model.coef_).flatten()
        else:
            LOG.warning(
                f"Model {model_type} does not have feature_importances_ or coef_. Skipping tree importance."
            )
            self.tree_model_name = None
            return

        if len(importances) != len(processed_features):
            LOG.warning(
                f"Importances ({len(importances)}) != features ({len(processed_features)}). Skipping tree importance."
            )
            self.tree_model_name = None
            return

        feature_importances = pd.DataFrame(
            {"Feature": processed_features, "Importance": importances}
        ).sort_values(by="Importance", ascending=False)
        plt.figure(figsize=(10, 6))
        plt.barh(feature_importances["Feature"], feature_importances["Importance"])
        plt.xlabel("Importance")
        plt.title(f"Feature Importance ({model_type})")
        plot_path = os.path.join(self.output_dir, "tree_importance.png")
        plt.savefig(plot_path, bbox_inches="tight")
        plt.close()
        self.plots["tree_importance"] = plot_path

    def save_shap_values(self, max_samples=None, max_display=None, max_features=None):
        model = self.best_model or self.exp.get_config("best_model")

        X_data = None
        for key in ("X_test_transformed", "X_train_transformed"):
            try:
                X_data = self.exp.get_config(key)
                break
            except KeyError:
                continue
        if X_data is None:
            raise RuntimeError("No transformed dataset found for SHAP.")

        # --- Adaptive feature limiting (proportional cap) ---
        n_rows, n_features = X_data.shape
        if max_features is None:
            if n_features <= 200:
                max_features = n_features
            else:
                max_features = min(200, max(20, int(n_features * 0.1)))

        try:
            if hasattr(model, "feature_importances_"):
                importances = pd.Series(
                    model.feature_importances_, index=X_data.columns
                )
                top_features = importances.nlargest(max_features).index
            elif hasattr(model, "coef_"):
                coef = abs(model.coef_).flatten()
                importances = pd.Series(coef, index=X_data.columns)
                top_features = importances.nlargest(max_features).index
            else:
                variances = X_data.var()
                top_features = variances.nlargest(max_features).index

            if len(top_features) < n_features:
                LOG.info(
                    f"Restricted SHAP computation to top {len(top_features)} / {n_features} features"
                )
            X_data = X_data[top_features]
        except Exception as e:
            LOG.warning(
                f"Feature limiting failed: {e}. Using all {n_features} features."
            )

        # --- Adaptive row subsampling ---
        if max_samples is None:
            if n_rows <= 500:
                max_samples = n_rows
            elif n_rows <= 5000:
                max_samples = 500
            else:
                max_samples = min(1000, int(n_rows * 0.1))

        if n_rows > max_samples:
            LOG.info(f"Subsampling SHAP rows: {max_samples} of {n_rows}")
            X_data = X_data.sample(max_samples, random_state=42)

        # --- Adaptive feature display ---
        if max_display is None:
            if X_data.shape[1] <= 20:
                max_display = X_data.shape[1]
            elif X_data.shape[1] <= 100:
                max_display = 30
            else:
                max_display = 50

        # Background set
        bg = X_data.sample(min(len(X_data), 100), random_state=42)
        predict_fn = (
            model.predict_proba if hasattr(model, "predict_proba") else model.predict
        )

        # Optimized explainer
        if hasattr(model, "feature_importances_"):
            explainer = shap.TreeExplainer(
                model, bg, feature_perturbation="tree_path_dependent", n_jobs=-1
            )
        elif hasattr(model, "coef_"):
            explainer = shap.LinearExplainer(model, bg)
        else:
            explainer = shap.Explainer(predict_fn, bg)

        try:
            shap_values = explainer(X_data)
            self.shap_model_name = explainer.__class__.__name__
        except Exception as e:
            LOG.error(f"SHAP computation failed: {e}")
            self.shap_model_name = None
            return

        # --- Plot SHAP summary ---
        out_path = os.path.join(self.output_dir, "shap_summary.png")
        plt.figure()
        shap.plots.beeswarm(shap_values, max_display=max_display, show=False)
        plt.title(
            f"SHAP Summary for {model.__class__.__name__} (top {max_display} features)"
        )
        plt.savefig(out_path, bbox_inches="tight")
        plt.close()
        self.plots["shap_summary"] = out_path

        # --- Log summary ---
        LOG.info(
            f"SHAP summary completed with {X_data.shape[0]} rows and {X_data.shape[1]} features (displaying top {max_display})."
        )

    def generate_html_report(self):
        LOG.info("Generating HTML report")
        plots_html = ""
        for plot_name, plot_path in self.plots.items():
            if plot_name == "tree_importance" and not getattr(
                self, "tree_model_name", None
            ):
                continue
            encoded_image = self.encode_image_to_base64(plot_path)
            if plot_name == "tree_importance" and getattr(
                self, "tree_model_name", None
            ):
                section_title = f"Feature importance from {self.tree_model_name}"
            elif plot_name == "shap_summary":
                section_title = (
                    f"SHAP Summary from {getattr(self, 'shap_model_name', 'model')}"
                )
            else:
                section_title = plot_name
            plots_html += f"""
            <div class="plot" id="{plot_name}">
                <h2>{section_title}</h2>
                <img src="data:image/png;base64,{encoded_image}" alt="{plot_name}">
            </div>
            """
        return f"{plots_html}"

    def encode_image_to_base64(self, img_path):
        with open(img_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode("utf-8")

    def run(self):
        if (
            self.exp is None
            or not hasattr(self.exp, "is_setup")
            or not self.exp.is_setup
        ):
            self.setup_pycaret()
        self.save_tree_importance()
        self.save_shap_values()
        return self.generate_html_report()
