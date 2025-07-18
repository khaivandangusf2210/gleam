import base64
import logging
import os
import tempfile

import h5py
import joblib
import numpy as np
import pandas as pd
from feature_help_modal import get_feature_metrics_help_modal
from feature_importance import FeatureImportanceAnalyzer
from sklearn.metrics import average_precision_score
from utils import get_html_closing, get_html_template

logging.basicConfig(level=logging.DEBUG)
LOG = logging.getLogger(__name__)


class BaseModelTrainer:
    def __init__(
        self,
        input_file,
        target_col,
        output_dir,
        task_type,
        random_seed,
        test_file=None,
        **kwargs,
    ):
        self.exp = None  # This will be set in the subclass
        self.input_file = input_file
        self.target_col = target_col
        self.output_dir = output_dir
        self.task_type = task_type
        self.random_seed = random_seed
        self.data = None
        self.target = None
        self.best_model = None
        self.results = None
        self.features_name = None
        self.plots = {}
        self.expaliner = None
        self.plots_explainer_html = None
        self.trees = []
        for key, value in kwargs.items():
            setattr(self, key, value)
        self.setup_params = {}
        self.test_file = test_file
        self.test_data = None

        if not self.output_dir:
            raise ValueError("output_dir must be specified and not None")

        LOG.info(f"Model kwargs: {self.__dict__}")

    def load_data(self):
        LOG.info(f"Loading data from {self.input_file}")
        self.data = pd.read_csv(self.input_file, sep=None, engine="python")
        self.data.columns = self.data.columns.str.replace(".", "_")

        # Remove prediction_label if present
        if "prediction_label" in self.data.columns:
            self.data = self.data.drop(columns=["prediction_label"])

        numeric_cols = self.data.select_dtypes(include=["number"]).columns
        non_numeric_cols = self.data.select_dtypes(exclude=["number"]).columns

        self.data[numeric_cols] = self.data[numeric_cols].apply(
            pd.to_numeric, errors="coerce"
        )

        if len(non_numeric_cols) > 0:
            LOG.info(f"Non-numeric columns found: {non_numeric_cols.tolist()}")

        names = self.data.columns.to_list()
        target_index = int(self.target_col) - 1
        self.target = names[target_index]
        self.features_name = [name for i, name in enumerate(names) if i != target_index]
        if hasattr(self, "missing_value_strategy"):
            if self.missing_value_strategy == "mean":
                self.data = self.data.fillna(self.data.mean(numeric_only=True))
            elif self.missing_value_strategy == "median":
                self.data = self.data.fillna(self.data.median(numeric_only=True))
            elif self.missing_value_strategy == "drop":
                self.data = self.data.dropna()
        else:
            # Default strategy if not specified
            self.data = self.data.fillna(self.data.median(numeric_only=True))

        if self.test_file:
            LOG.info(f"Loading test data from {self.test_file}")
            self.test_data = pd.read_csv(self.test_file, sep=None, engine="python")
            self.test_data = self.test_data[numeric_cols].apply(
                pd.to_numeric, errors="coerce"
            )
            self.test_data.columns = self.test_data.columns.str.replace(".", "_")

    def setup_pycaret(self):
        LOG.info("Initializing PyCaret")
        self.setup_params = {
            "target": self.target,
            "session_id": self.random_seed,
            "html": True,
            "log_experiment": False,
            "system_log": False,
            "index": False,
        }

        if self.test_data is not None:
            self.setup_params["test_data"] = self.test_data

        if (
            hasattr(self, "train_size")
            and self.train_size is not None
            and self.test_data is None
        ):
            self.setup_params["train_size"] = self.train_size

        if hasattr(self, "normalize") and self.normalize is not None:
            self.setup_params["normalize"] = self.normalize

        if hasattr(self, "feature_selection") and self.feature_selection is not None:
            self.setup_params["feature_selection"] = self.feature_selection

        if (
            hasattr(self, "cross_validation")
            and self.cross_validation is not None
            and self.cross_validation is False
        ):
            logging.info(
                "cross_validation is set to False. This will disable cross-validation."
            )

        if hasattr(self, "cross_validation") and self.cross_validation:
            if hasattr(self, "cross_validation_folds"):
                self.setup_params["fold"] = self.cross_validation_folds

        if hasattr(self, "remove_outliers") and self.remove_outliers is not None:
            self.setup_params["remove_outliers"] = self.remove_outliers

        if (
            hasattr(self, "remove_multicollinearity")
            and self.remove_multicollinearity is not None
        ):
            self.setup_params["remove_multicollinearity"] = (
                self.remove_multicollinearity
            )

        if (
            hasattr(self, "polynomial_features")
            and self.polynomial_features is not None
        ):
            self.setup_params["polynomial_features"] = self.polynomial_features

        if hasattr(self, "fix_imbalance") and self.fix_imbalance is not None:
            self.setup_params["fix_imbalance"] = self.fix_imbalance

        LOG.info(self.setup_params)

        # Solution: instantiate the correct PyCaret experiment based on task_type
        if self.task_type == "classification":
            from pycaret.classification import ClassificationExperiment

            self.exp = ClassificationExperiment()
        elif self.task_type == "regression":
            from pycaret.regression import RegressionExperiment

            self.exp = RegressionExperiment()
        else:
            raise ValueError("task_type must be 'classification' or 'regression'")

        self.exp.setup(self.data, **self.setup_params)

    def train_model(self):
        LOG.info("Training and selecting the best model")
        if self.task_type == "classification":
            average_displayed = "Weighted"
            self.exp.add_metric(
                id=f"PR-AUC-{average_displayed}",
                name=f"PR-AUC-{average_displayed}",
                target="pred_proba",
                score_func=average_precision_score,
                average="weighted",
            )

        if hasattr(self, "models") and self.models is not None:
            self.best_model = self.exp.compare_models(include=self.models, cross_validation=self.cross_validation)
        else:
            self.best_model = self.exp.compare_models(cross_validation=self.cross_validation)
        self.results = self.exp.pull()

        if self.task_type == "classification":
            self.results.rename(columns={"AUC": "ROC-AUC"}, inplace=True)

        _ = self.exp.predict_model(self.best_model)
        self.test_result_df = self.exp.pull()
        if self.task_type == "classification":
            self.test_result_df.rename(columns={"AUC": "ROC-AUC"}, inplace=True)

    def save_model(self):
        hdf5_model_path = "pycaret_model.h5"
        with h5py.File(hdf5_model_path, "w") as f:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                joblib.dump(self.best_model, temp_file.name)
                temp_file.seek(0)
                model_bytes = temp_file.read()
            f.create_dataset("model", data=np.void(model_bytes))

    def generate_plots(self):
        raise NotImplementedError("Subclasses should implement this method")

    def encode_image_to_base64(self, img_path):
        with open(img_path, "rb") as img_file:
            return base64.b64encode(img_file.read()).decode("utf-8")

    def save_html_report(self):
        LOG.info("Saving HTML report")

        if not self.output_dir:
            raise ValueError("output_dir must be specified and not None")

        model_name = type(self.best_model).__name__
        excluded_params = ["html", "log_experiment", "system_log", "test_data"]
        filtered_setup_params = {
            k: v for k, v in self.setup_params.items() if k not in excluded_params
        }
        setup_params_table = pd.DataFrame(
            list(filtered_setup_params.items()), columns=["Parameter", "Value"]
        )

        best_model_params = pd.DataFrame(
            self.best_model.get_params().items(), columns=["Parameter", "Value"]
        )
        best_model_params.to_csv(
            os.path.join(self.output_dir, "best_model.csv"), index=False
        )
        self.results.to_csv(os.path.join(self.output_dir, "comparison_results.csv"))
        self.test_result_df.to_csv(os.path.join(self.output_dir, "test_results.csv"))

        plots_html = ""
        length = len(self.plots)
        for i, (plot_name, plot_path) in enumerate(self.plots.items()):
            encoded_image = self.encode_image_to_base64(plot_path)
            plots_html += (
                f'<div class="plot">'
                f"<h3>{plot_name.capitalize()}</h3>"
                f'<img src="data:image/png;base64,{encoded_image}" alt="{plot_name}">'
                f"</div>"
            )
            if i < length - 1:
                plots_html += "<hr>"

        tree_plots = ""
        for i, tree in enumerate(self.trees):
            if tree:
                tree_plots += (
                    f'<div class="plot">'
                    f"<h3>Tree {i + 1}</h3>"
                    f'<img src="data:image/png;base64,{tree}" alt="tree {i + 1}">'
                    f"</div>"
                )

        analyzer = FeatureImportanceAnalyzer(
            data=self.data,
            target_col=self.target_col,
            task_type=self.task_type,
            output_dir=self.output_dir,
            exp=self.exp,
            best_model=self.best_model,
        )
        feature_importance_html = analyzer.run()

        # --- Feature Metrics Help Button ---
        feature_metrics_button_html = (
            '<button class="help-modal-btn" id="openFeatureMetricsHelp" style="margin-bottom:12px;">'
            "Help: Metrics Guide"
            "</button>"
            "<style>"
            ".help-modal-btn {"
            "background-color: #17623b;"
            "color: #fff;"
            "border: none;"
            "border-radius: 24px;"
            "padding: 10px 28px;"
            "font-size: 1.1rem;"
            "font-weight: bold;"
            "letter-spacing: 0.03em;"
            "cursor: pointer;"
            "transition: background 0.2s, box-shadow 0.2s;"
            "box-shadow: 0 2px 8px rgba(23,98,59,0.07);"
            "}"
            ".help-modal-btn:hover, .help-modal-btn:focus {"
            "background-color: #21895e;"
            "outline: none;"
            "box-shadow: 0 4px 16px rgba(23,98,59,0.14);"
            "}"
            "</style>"
        )

        html_content = (
            f"{get_html_template()}"
            "<h1>Tabular Learner Model Report</h1>"
            f"{feature_metrics_button_html}"
            '<div class="tabs">'
            '<div class="tab" onclick="openTab(event, \'summary\')">'
            "Validation Result Summary & Config</div>"
            '<div class="tab" onclick="openTab(event, \'plots\')">'
            "Test Results</div>"
            '<div class="tab" onclick="openTab(event, \'feature\')">'
            "Feature Importance</div>"
        )
        if self.plots_explainer_html:
            html_content += (
                '<div class="tab" onclick="openTab(event, \'explainer\')">'
                "Explainer Plots</div>"
            )
        html_content += (
            "</div>"
            '<div id="summary" class="tab-content">'
            f"<h2>Model Metrics from {'Cross-Validation Set' if self.cross_validation else 'Validation set'}</h2>"
            f"<h2>Best Model: {model_name}</h2>"
            "<h5>The best model is selected by: Accuracy (Classification)"
            " or R2 (Regression).</h5>"
            f"{self.results.to_html(index=False, classes='table sortable')}"
            "<h2>Best Model's Hyperparameters</h2>"
            f"{best_model_params.to_html(index=False, header=True, classes='table sortable')}"
            "<h2>Setup Parameters</h2>"
            f"{setup_params_table.to_html(index=False, header=True, classes='table sortable')}"
            "<h5>If you want to know all the experiment setup parameters,"
            " please check the PyCaret documentation for"
            " the classification/regression <code>exp</code> function.</h5>"
            "</div>"
            '<div id="plots" class="tab-content">'
            f"<h2>Best Model: {model_name}</h2>"
            "<h5>The best model is selected by: Accuracy (Classification)"
            " or R2 (Regression).</h5>"
            "<h2>Test Metrics</h2>"
            f"{self.test_result_df.to_html(index=False)}"
            "<h2>Test Results</h2>"
            f"{plots_html}"
            "</div>"
            '<div id="feature" class="tab-content">'
            f"{feature_importance_html}"
            "</div>"
        )
        if self.plots_explainer_html:
            html_content += (
                '<div id="explainer" class="tab-content">'
                f"{self.plots_explainer_html}"
                f"{tree_plots}"
                "</div>"
            )
        html_content += (
            "<script>"
            "document.addEventListener(\"DOMContentLoaded\", function() {"
            "var tables = document.querySelectorAll(\"table.sortable\");"
            "tables.forEach(function(table) {"
            "var headers = table.querySelectorAll(\"th\");"
            "headers.forEach(function(header, index) {"
            "header.style.cursor = \"pointer\";"
            "// Add initial arrow (up) to indicate sortability, use Unicode ↑ (U+2191)"
            "header.innerHTML += '<span class=\"sort-arrow\"> ↑</span>';"
            "header.addEventListener(\"click\", function() {"
            "var direction = this.getAttribute("
            "\"data-sort-direction\""
            ") || \"asc\";"
            "// Reset arrows in all headers of this table"
            "headers.forEach(function(h) {"
            "var arrow = h.querySelector(\".sort-arrow\");"
            "if (arrow) arrow.textContent = \" ↑\";"
            "});"
            "// Set arrow for clicked header"
            "var arrow = this.querySelector(\".sort-arrow\");"
            "arrow.textContent = direction === \"asc\" ? \" ↓\" : \" ↑\";"
            "sortTable(table, index, direction);"
            "this.setAttribute(\"data-sort-direction\","
            "direction === \"asc\" ? \"desc\" : \"asc\");"
            "});"
            "});"
            "});"
            "});"
            "function sortTable(table, colNum, direction) {"
            "var tb = table.tBodies[0];"
            "var tr = Array.prototype.slice.call(tb.rows, 0);"
            "var multiplier = direction === \"asc\" ? 1 : -1;"
            "tr = tr.sort(function(a, b) {"
            "var aText = a.cells[colNum].textContent.trim();"
            "var bText = b.cells[colNum].textContent.trim();"
            "// Remove arrow from text comparison"
            "aText = aText.replace(/[↑↓]/g, '').trim();"
            "bText = bText.replace(/[↑↓]/g, '').trim();"
            "if (!isNaN(aText) && !isNaN(bText)) {"
            "return multiplier * ("
            "parseFloat(aText) - parseFloat(bText)"
            ");"
            "} else {"
            "return multiplier * aText.localeCompare(bText);"
            "}"
            "});"
            "for (var i = 0; i < tr.length; ++i) tb.appendChild(tr[i]);"
            "}"
            "</script>"
        )
        # --- Add the Feature Metrics Help Modal ---
        html_content += get_feature_metrics_help_modal()
        html_content += f"{get_html_closing()}"
        with open(
            os.path.join(self.output_dir, "comparison_result.html"),
            "w",
            encoding="utf-8",
        ) as file:
            file.write(html_content)

    def save_dashboard(self):
        raise NotImplementedError("Subclasses should implement this method")

    def generate_plots_explainer(self):
        raise NotImplementedError("Subclasses should implement this method")

    def generate_tree_plots(self):
        from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
        from xgboost import XGBClassifier, XGBRegressor
        from explainerdashboard.explainers import RandomForestExplainer

        LOG.info("Generating tree plots")
        X_test = self.exp.X_test_transformed.copy()
        y_test = self.exp.y_test_transformed

        is_rf = isinstance(
            self.best_model, (RandomForestClassifier, RandomForestRegressor)
        )
        is_xgb = isinstance(self.best_model, (XGBClassifier, XGBRegressor))

        num_trees = None
        if is_rf:
            num_trees = self.best_model.n_estimators
        elif is_xgb:
            num_trees = len(self.best_model.get_booster().get_dump())
        else:
            LOG.warning("Tree plots not supported for this model type.")
            return

        try:
            explainer = RandomForestExplainer(self.best_model, X_test, y_test)
            for i in range(num_trees):
                fig = explainer.decisiontree_encoded(tree_idx=i, index=0)
                LOG.info(f"Tree {i + 1}")
                LOG.info(fig)
                self.trees.append(fig)
        except Exception as e:
            LOG.error(f"Error generating tree plots: {e}")

    def run(self):
        self.load_data()
        self.setup_pycaret()
        self.train_model()
        self.save_model()
        self.generate_plots()
        self.generate_plots_explainer()
        self.generate_tree_plots()
        self.save_html_report()
        # self.save_dashboard()
