"""Pipeline subclass for all binary classification pipelines."""

import numpy as np
import pandas as pd
import woodwork as ww

from evalml.objectives import get_objective
from evalml.pipelines.binary_classification_pipeline import BinaryClassificationPipeline
from evalml.pipelines.ensemble_pipeline_base import EnsemblePipelineBase
from evalml.pipelines.multiclass_classification_pipeline import (
    MulticlassClassificationPipeline,
)
from evalml.problem_types import ProblemTypes
from evalml.problem_types.utils import is_binary, is_multiclass
from evalml.utils import infer_feature_types


class EnsembleClassificationPipeline(EnsemblePipelineBase):
    """Pipeline subclass for all binary classification pipelines.

    Args:
        component_graph (ComponentGraph, list, dict): ComponentGraph instance, list of components in order, or dictionary of components.
            Accepts strings or ComponentBase subclasses in the list.
            Note that when duplicate components are specified in a list, the duplicate component names will be modified with the
            component's index in the list. For example, the component graph
            [Imputer, One Hot Encoder, Imputer, Logistic Regression Classifier] will have names
            ["Imputer", "One Hot Encoder", "Imputer_2", "Logistic Regression Classifier"]
        parameters (dict): Dictionary with component names as keys and dictionary of that component's parameters as values.
             An empty dictionary or None implies using all default values for component parameters. Defaults to None.
        custom_name (str): Custom name for the pipeline. Defaults to None.
        random_seed (int): Seed for the random number generator. Defaults to 0.

    Example:
        >>> pipeline = BinaryClassificationPipeline(component_graph=["Simple Imputer", "Logistic Regression Classifier"],
        ...                                         parameters={"Logistic Regression Classifier": {"penalty": "elasticnet",
        ...                                                                                        "solver": "liblinear"}},
        ...                                         custom_name="My Binary Pipeline")
        ...
        >>> assert pipeline.custom_name == "My Binary Pipeline"
        >>> assert pipeline.component_graph.component_dict.keys() == {'Simple Imputer', 'Logistic Regression Classifier'}

        The pipeline parameters will be chosen from the default parameters for every component, unless specific parameters
        were passed in as they were above.

        >>> assert pipeline.parameters == {
        ...     'Simple Imputer': {'impute_strategy': 'most_frequent', 'fill_value': None},
        ...     'Logistic Regression Classifier': {'penalty': 'elasticnet',
        ...                                        'C': 1.0,
        ...                                        'n_jobs': -1,
        ...                                        'multi_class': 'auto',
        ...                                        'solver': 'liblinear'}}
    """

    name = "V3 Classification Ensemble Pipeline"

    def __init__(
        self,
        input_pipelines,
        component_graph=None,
        parameters=None,
        custom_name=None,
        cv_valid_data=None,
        random_seed=0,
    ):
        if component_graph is None:
            component_graph = {
                "Label Encoder": ["Label Encoder", "X", "y"],
                "Stacked Ensembler": [
                    "Stacked Ensemble Classifier",
                    "X",
                    "Label Encoder.y",
                ],
            }
        super().__init__(
            input_pipelines=input_pipelines,
            component_graph=component_graph,
            custom_name=custom_name,
            parameters=parameters,
            cv_valid_data=cv_valid_data,
            random_seed=random_seed,
        )

    def _predict(self, X, objective=None):
        """Make predictions using selected features.

        Args:
            X (pd.DataFrame): Data of shape [n_samples, n_features]
            objective (Object or string): The objective to use to make predictions.

        Returns:
            pd.Series: Estimated labels
        """
        metalearner_X = self.transform(X)
        return super()._predict(metalearner_X, objective=objective)

    def predict_proba(self, X, X_train=None, y_train=None):
        """Make predictions using selected features.

        Args:
            X (pd.DataFrame): Data of shape [n_samples, n_features]
            objective (Object or string): The objective to use to make predictions.

        Returns:
            pd.Series: Estimated labels
        """

        metalearner_X = self.transform(X)
        return super().predict_proba(metalearner_X)

    def _preds_processor(self, preds, pipeline_name):
        if not isinstance(preds, pd.DataFrame):
            raise ValueError("Preds must be in the form of a pd.Dataframe")
        new_columns = {}
        for i, column in enumerate(preds.columns):
            new_columns[column] = i
        preds.ww.rename(new_columns, inplace=True)
        if len(preds.columns) == 2:
            # If it is a binary problem, drop the first column since both columns are colinear
            preds = preds.ww.drop(preds.columns[0])
        preds = preds.ww.rename(
            {col: f"Col {str(col)} {pipeline_name}.x" for col in preds.columns},
        )
        return preds

    def fit(self, X, y, data_splitter=None, force_retrain=False):
        """Build a classification model. For string and categorical targets, classes are sorted by sorted(set(y)) and then are mapped to values between 0 and n_classes-1.

        Args:
            X (pd.DataFrame or np.ndarray): The input training data of shape [n_samples, n_features]
            y (pd.Series, np.ndarray): The target training labels of length [n_samples]

        Returns:
            self

        Raises:
            ValueError: If the number of unique classes in y are not appropriate for the type of pipeline.
        """

        X = infer_feature_types(X)
        y = infer_feature_types(y)

        if is_binary(self.problem_type) and y.nunique() != 2:
            raise ValueError("Binary pipelines require y to have 2 unique classes!")
        elif is_multiclass(self.problem_type) and y.nunique() in [1, 2]:
            raise ValueError(
                "Multiclass pipelines require y to have 3 or more unique classes!",
            )

        if not self._all_input_pipelines_fitted or force_retrain is True:
            self._fit_input_pipelines(X, y, force_retrain=True)

        metalearner_X = []
        metalearner_y = []

        if self.cv_valid_data and force_retrain is False:
            for pipeline_name, cv_valid_data in self.cv_valid_data.items():
                pl_valid_preds = []
                for X, preds in cv_valid_data:
                    pl_valid_preds.append(self._preds_processor(preds, pipeline_name))

                pl_all_preds = pd.concat(pl_valid_preds)
                metalearner_X.append(pl_all_preds)

            metalearner_X = ww.concat_columns(metalearner_X)
            metalearner_y = y
            if len(metalearner_X) != len(metalearner_y):
                metalearner_X = metalearner_X.loc[metalearner_y.index]

        else:
            if data_splitter is None:
                from evalml.automl.utils import make_data_splitter

                data_splitter = make_data_splitter(
                    X,
                    y,
                    problem_type=ProblemTypes.BINARY,
                )

            splits = data_splitter.split(X, y)

            metalearner_X = []
            metalearner_y = []

            pred_pls = []
            for pipeline in self.input_pipelines:
                pred_pls.append(pipeline.clone())

            # Split off pipelines for CV
            for i, (train, valid) in enumerate(splits):
                fold_X = []
                X_train, X_valid = X.ww.iloc[train], X.ww.iloc[valid]
                y_train, y_valid = y.ww.iloc[train], y.ww.iloc[valid]

                for pipeline in pred_pls:
                    pipeline.fit(X_train, y_train)
                    pl_preds = pipeline.predict_proba(X_valid)
                    fold_X.append(self._preds_processor(pl_preds, pipeline.name))

                metalearner_X.append(ww.concat_columns(fold_X))
                metalearner_y.append(y_valid)

            metalearner_X = pd.concat(metalearner_X)
            metalearner_y = pd.concat(metalearner_y)

        self.component_graph.fit(metalearner_X, metalearner_y)

        self._classes_ = list(ww.init_series(np.unique(metalearner_y)))
        return self

    def transform(self, X, y=None):
        if not self._all_input_pipelines_fitted:
            raise ValueError("Input pipelines needs to be fitted before transform")
        input_pipeline_preds = []
        for pipeline in self.input_pipelines:
            pl_preds = pipeline.predict_proba(X)
            input_pipeline_preds.append(self._preds_processor(pl_preds, pipeline.name))

        return ww.concat_columns(input_pipeline_preds)


class EnsembleBinaryClassificationPipeline(
    EnsembleClassificationPipeline,
    BinaryClassificationPipeline,
):
    """Pipeline subclass for all binary classification pipelines.

    Args:
        component_graph (ComponentGraph, list, dict): ComponentGraph instance, list of components in order, or dictionary of components.
            Accepts strings or ComponentBase subclasses in the list.
            Note that when duplicate components are specified in a list, the duplicate component names will be modified with the
            component's index in the list. For example, the component graph
            [Imputer, One Hot Encoder, Imputer, Logistic Regression Classifier] will have names
            ["Imputer", "One Hot Encoder", "Imputer_2", "Logistic Regression Classifier"]
        parameters (dict): Dictionary with component names as keys and dictionary of that component's parameters as values.
             An empty dictionary or None implies using all default values for component parameters. Defaults to None.
        custom_name (str): Custom name for the pipeline. Defaults to None.
        random_seed (int): Seed for the random number generator. Defaults to 0.

    Example:
        >>> pipeline = BinaryClassificationPipeline(component_graph=["Simple Imputer", "Logistic Regression Classifier"],
        ...                                         parameters={"Logistic Regression Classifier": {"penalty": "elasticnet",
        ...                                                                                        "solver": "liblinear"}},
        ...                                         custom_name="My Binary Pipeline")
        ...
        >>> assert pipeline.custom_name == "My Binary Pipeline"
        >>> assert pipeline.component_graph.component_dict.keys() == {'Simple Imputer', 'Logistic Regression Classifier'}

        The pipeline parameters will be chosen from the default parameters for every component, unless specific parameters
        were passed in as they were above.

        >>> assert pipeline.parameters == {
        ...     'Simple Imputer': {'impute_strategy': 'most_frequent', 'fill_value': None},
        ...     'Logistic Regression Classifier': {'penalty': 'elasticnet',
        ...                                        'C': 1.0,
        ...                                        'n_jobs': -1,
        ...                                        'multi_class': 'auto',
        ...                                        'solver': 'liblinear'}}
    """

    name = "V3 Binary Classification Ensemble Pipeline"


class EnsembleMulticlassClassificationPipeline(
    EnsembleClassificationPipeline,
    MulticlassClassificationPipeline,
):
    """Pipeline subclass for all binary classification pipelines.

    Args:
        component_graph (ComponentGraph, list, dict): ComponentGraph instance, list of components in order, or dictionary of components.
            Accepts strings or ComponentBase subclasses in the list.
            Note that when duplicate components are specified in a list, the duplicate component names will be modified with the
            component's index in the list. For example, the component graph
            [Imputer, One Hot Encoder, Imputer, Logistic Regression Classifier] will have names
            ["Imputer", "One Hot Encoder", "Imputer_2", "Logistic Regression Classifier"]
        parameters (dict): Dictionary with component names as keys and dictionary of that component's parameters as values.
             An empty dictionary or None implies using all default values for component parameters. Defaults to None.
        custom_name (str): Custom name for the pipeline. Defaults to None.
        random_seed (int): Seed for the random number generator. Defaults to 0.

    Example:
        >>> pipeline = BinaryClassificationPipeline(component_graph=["Simple Imputer", "Logistic Regression Classifier"],
        ...                                         parameters={"Logistic Regression Classifier": {"penalty": "elasticnet",
        ...                                                                                        "solver": "liblinear"}},
        ...                                         custom_name="My Binary Pipeline")
        ...
        >>> assert pipeline.custom_name == "My Binary Pipeline"
        >>> assert pipeline.component_graph.component_dict.keys() == {'Simple Imputer', 'Logistic Regression Classifier'}

        The pipeline parameters will be chosen from the default parameters for every component, unless specific parameters
        were passed in as they were above.

        >>> assert pipeline.parameters == {
        ...     'Simple Imputer': {'impute_strategy': 'most_frequent', 'fill_value': None},
        ...     'Logistic Regression Classifier': {'penalty': 'elasticnet',
        ...                                        'C': 1.0,
        ...                                        'n_jobs': -1,
        ...                                        'multi_class': 'auto',
        ...                                        'solver': 'liblinear'}}
    """

    name = "V3 Multiclass Classification Ensemble Pipeline"
