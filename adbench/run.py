import logging; logging.basicConfig(level=logging.WARNING)
import numpy as np
import pandas as pd
import itertools
from itertools import product
from tqdm import tqdm
import time
import gc
import os
from keras import backend as K

from adbench.datasets.data_generator import DataGenerator
from adbench.myutils import Utils

class RunPipeline():
    def __init__(self, suffix:str=None, mode:str='rla', parallel:str=None,
                 generate_duplicates=True, n_samples_threshold=1000,
                 realistic_synthetic_mode:str=None,
                 noise_type=None):
        '''
        :param suffix: saved file suffix (including the model performance result and model weights)
        :param mode: rla or nla —— ratio of labeled anomalies or number of labeled anomalies
        :param parallel: unsupervise, semi-supervise or supervise, choosing to parallelly run the code
        :param generate_duplicates: whether to generate duplicated samples when sample size is too small
        :param n_samples_threshold: threshold for generating the above duplicates, if generate_duplicates is False, then datasets with sample size smaller than n_samples_threshold will be dropped
        :param realistic_synthetic_mode: local, global, dependency or cluster —— whether to generate the realistic synthetic anomalies to test different algorithms
        :param noise_type: duplicated_anomalies, irrelevant_features or label_contamination —— whether to test the model robustness
        '''

        # utils function
        self.utils = Utils()

        self.mode = mode
        self.parallel = parallel

        # global parameters
        self.generate_duplicates = generate_duplicates
        self.n_samples_threshold = n_samples_threshold

        self.realistic_synthetic_mode = realistic_synthetic_mode
        self.noise_type = noise_type

        # the suffix of all saved files
        self.suffix = suffix + '_' + 'type(' + str(realistic_synthetic_mode) + ')_' + 'noise(' + str(noise_type) + ')_'\
                      + self.parallel

        # data generator instantiation
        self.data_generator = DataGenerator(generate_duplicates=self.generate_duplicates,
                                            n_samples_threshold=self.n_samples_threshold)

        # ratio of labeled anomalies
        if self.noise_type is not None:
            self.rla_list = [1.00]
        else:
            self.rla_list = [0.00, 0.01, 0.05, 0.10, 0.25, 0.50, 0.75, 1.00]

        # number of labeled anomalies
        self.nla_list = [0, 1, 5, 10, 25, 50, 75, 100]
        # seed list
        self.seed_list = [int(i) for i in (np.arange(3) + 1)]


        if self.noise_type is None:
            pass

        elif self.noise_type == 'duplicated_anomalies':
            self.noise_params_list = [1, 2, 3, 4, 5, 6]

        elif self.noise_type == 'irrelevant_features':
            self.noise_params_list = [0.00, 0.01, 0.05, 0.10, 0.25, 0.50]

        elif self.noise_type == 'label_contamination':
            self.noise_params_list = [0.00, 0.01, 0.05, 0.10, 0.25, 0.50]

        else:
            raise NotImplementedError

        # model_dict (model_name: clf)
        self.model_dict = {}

        # unsupervised algorithms
        if self.parallel == 'unsupervise':
            from adbench.baseline.unsupervised.PyOD import PYOD
            from adbench.baseline.unsupervised.DAGMM.run import DAGMM

            # from pyod
            for _ in ['IForest', 'OCSVM', 'CBLOF', 'COF', 'COPOD', 'ECOD', 'HBOS', 'KNN', 'LODA',
                      'LOF', 'PCA', 'SOD', 'DeepSVDD']:
                self.model_dict[_] = PYOD

            # DAGMM
            self.model_dict['DAGMM'] = DAGMM

        # semi-supervised algorithms
        elif self.parallel == 'semi-supervise':
            from adbench.baseline.semisupervised.PyOD import PYOD
            from adbench.baseline.semisupervised.GANomaly.run import GANomaly
            from adbench.baseline.semisupervised.DeepSAD.src.run import DeepSAD
            from adbench.baseline.semisupervised.REPEN.run import REPEN
            from adbench.baseline.semisupervised.DevNet.run import DevNet
            from adbench.baseline.semisupervised.PReNet.run import PReNet
            from adbench.baseline.semisupervised.FEAWAD.run import FEAWAD

            self.model_dict = {'GANomaly': GANomaly,
                               'DeepSAD': DeepSAD,
                               'REPEN': REPEN,
                               'DevNet': DevNet,
                               'PReNet': PReNet,
                               'FEAWAD': FEAWAD,
                               'XGBOD': PYOD}

        # fully-supervised algorithms
        elif self.parallel == 'supervise':
            from adbench.baseline.supervised.Supervised import supervised
            from adbench.baseline.supervised.FTTransformer.run import FTTransformer

            # from sklearn
            for _ in ['NB', 'SVM', 'MLP', 'RF', 'LGB', 'XGB', 'CatB']:
                self.model_dict[_] = supervised
            # ResNet and FTTransformer for tabular data
            for _ in ['ResNet', 'FTTransformer']:
                self.model_dict[_] = FTTransformer

        else:
            raise NotImplementedError

    # dataset filter for delelting those datasets that do not satisfy the experimental requirement
    def dataset_filter(self):
        # dataset list in the current folder
        dataset_list_org = self.data_generator.generate_dataset_list()


        dataset_list, dataset_size = [], []
        for dataset in dataset_list_org:
            add = True
            for seed in self.seed_list:
                self.data_generator.seed = seed
                self.data_generator.dataset = dataset
                data = self.data_generator.generator(la=1.00, at_least_one_labeled=True)

                if not self.generate_duplicates and len(data['y_train']) + len(data['y_test']) < self.n_samples_threshold:
                    add = False

                else:
                    if self.mode == 'nla' and sum(data['y_train']) >= self.nla_list[-1]:
                        pass

                    elif self.mode == 'rla' and sum(data['y_train']) > 0:
                        pass

                    else:
                        add = False

            if add:
                dataset_list.append(dataset)
                dataset_size.append(len(data['y_train']) + len(data['y_test']))
            else:
                print(f"remove the dataset {dataset}")

        # sort datasets by their sample size
        dataset_list = [dataset_list[_] for _ in np.argsort(np.array(dataset_size))]

        return dataset_list

    # model fitting function
    def model_fit(self):
        try:
            # model initialization, if model weights are saved, the save_suffix should be specified
            if self.model_name in ['DevNet', 'FEAWAD', 'REPEN']:
                self.clf = self.clf(seed=self.seed, model_name=self.model_name, save_suffix=self.suffix)
            else:
                self.clf = self.clf(seed=self.seed, model_name=self.model_name)

        except Exception as error:
            print(f'Error in model initialization. Model:{self.model_name}, Error: {error}')
            pass

        try:
            # fitting
            start_time = time.time()
            self.clf = self.clf.fit(X_train=self.data['X_train'], y_train=self.data['y_train'])
            end_time = time.time(); time_fit = end_time - start_time

            # predicting score (inference)
            start_time = time.time()
            if self.model_name == 'DAGMM':
                score_test = self.clf.predict_score(self.data['X_train'], self.data['X_test'])
            else:
                score_test = self.clf.predict_score(self.data['X_test'])
            end_time = time.time(); time_inference = end_time - start_time

            # performance
            result = self.utils.metric(y_true=self.data['y_test'], y_score=score_test)

            K.clear_session()
            print(f"Model: {self.model_name}, AUC-ROC: {result['aucroc']}, AUC-PR: {result['aucpr']}")

            del self.clf
            gc.collect()

        except Exception as error:
            print(f'Error in model fitting. Model:{self.model_name}, Error: {error}')
            time_fit, time_inference = None, None
            result = {'aucroc': np.nan, 'aucpr': np.nan}
            pass

        return time_fit, time_inference, result

    # run the experiments in ADBench
    def run(self, dataset=None, clf=None):
        if dataset is None:
            #  filteting dataset that does not meet the experimental requirements
            dataset_list = self.dataset_filter()
            X, y = None, None
        else:
            isinstance(dataset, dict)
            dataset_list = [None]
            X = dataset['X']; y = dataset['y']

        # experimental parameters
        if self.mode == 'nla':
            if self.noise_type is not None:
                experiment_params = list(product(dataset_list, self.nla_list, self.noise_params_list, self.seed_list))
            else:
                experiment_params = list(product(dataset_list, self.nla_list, self.seed_list))
        else:
            if self.noise_type is not None:
                experiment_params = list(product(dataset_list, self.rla_list, self.noise_params_list, self.seed_list))
            else:
                experiment_params = list(product(dataset_list, self.rla_list, self.seed_list))

        print(f'{len(dataset_list)} datasets, {len(self.model_dict.keys())} models')

        # save the results
        print(f"Experiment results are saved at: {os.path.join(os.path.dirname(os.path.abspath(__file__)), 'result')}")
        os.makedirs(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'result'), exist_ok=True)
        columns = list(self.model_dict.keys()) if clf is None else ['Customized']
        df_AUCROC = pd.DataFrame(data=None, index=experiment_params, columns=columns)
        df_AUCPR = pd.DataFrame(data=None, index=experiment_params, columns=columns)
        df_time_fit = pd.DataFrame(data=None, index=experiment_params, columns=columns)
        df_time_inference = pd.DataFrame(data=None, index=experiment_params, columns=columns)

        results = []
        for i, params in tqdm(enumerate(experiment_params)):
            if self.noise_type is not None:
                dataset, la, noise_param, self.seed = params
            else:
                dataset, la, self.seed = params

            if self.parallel == 'unsupervise' and la != 0.0 and self.noise_type is None:
                continue

            # generate data
            self.data_generator.seed = self.seed
            self.data_generator.dataset = dataset

            try:
                if self.noise_type == 'duplicated_anomalies':
                    self.data = self.data_generator.generator(la=la, at_least_one_labeled=True, X=X, y=y,
                                                              realistic_synthetic_mode=self.realistic_synthetic_mode,
                                                              noise_type=self.noise_type, duplicate_times=noise_param)
                elif self.noise_type == 'irrelevant_features':
                    self.data = self.data_generator.generator(la=la, at_least_one_labeled=True, X=X, y=y,
                                                              realistic_synthetic_mode=self.realistic_synthetic_mode,
                                                              noise_type=self.noise_type, noise_ratio=noise_param)
                elif self.noise_type == 'label_contamination':
                    self.data = self.data_generator.generator(la=la, at_least_one_labeled=True, X=X, y=y,
                                                              realistic_synthetic_mode=self.realistic_synthetic_mode,
                                                              noise_type=self.noise_type, noise_ratio=noise_param)
                else:
                    self.data = self.data_generator.generator(la=la, at_least_one_labeled=True, X=X, y=y,
                                                              realistic_synthetic_mode=self.realistic_synthetic_mode)

            except Exception as error:
                print(f'Error when generating data: {error}')
                pass
                continue

            if clf is None:
                for model_name in tqdm(self.model_dict.keys()):
                    self.model_name = model_name
                    self.clf = self.model_dict[self.model_name]

                    # fit and test model
                    time_fit, time_inference, metrics = self.model_fit()
                    results.append([params, model_name, metrics, time_fit, time_inference])
                    print(f'Current experiment parameters: {params}, model: {model_name}, metrics: {metrics}, '
                          f'fitting time: {time_fit}, inference time: {time_inference}')

                    # store and save the result (AUC-ROC, AUC-PR and runtime / inference time)
                    df_AUCROC[model_name].iloc[i] = metrics['aucroc']
                    df_AUCPR[model_name].iloc[i] = metrics['aucpr']
                    df_time_fit[model_name].iloc[i] = time_fit
                    df_time_inference[model_name].iloc[i] = time_inference

                    df_AUCROC.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  'result', 'AUCROC_' + self.suffix + '.csv'), index=True)
                    df_AUCPR.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                 'result', 'AUCPR_' + self.suffix + '.csv'), index=True)
                    df_time_fit.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                    'result', 'Time(fit)_' + self.suffix + '.csv'), index=True)
                    df_time_inference.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                          'result', 'Time(inference)_' + self.suffix + '.csv'), index=True)

            else:
                self.clf = clf; self.model_name = 'Customized'
                # fit and test model
                time_fit, time_inference, metrics = self.model_fit()
                results.append([params, self.model_name, metrics, time_fit, time_inference])
                print(f'Current experiment parameters: {params}, model: {self.model_name}, metrics: {metrics}, '
                      f'fitting time: {time_fit}, inference time: {time_inference}')

                # store and save the result (AUC-ROC, AUC-PR and runtime / inference time)
                df_AUCROC[self.model_name].iloc[i] = metrics['aucroc']
                df_AUCPR[self.model_name].iloc[i] = metrics['aucpr']
                df_time_fit[self.model_name].iloc[i] = time_fit
                df_time_inference[self.model_name].iloc[i] = time_inference

                df_AUCROC.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                              'result', 'AUCROC_' + self.suffix + '.csv'), index=True)
                df_AUCPR.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                             'result', 'AUCPR_' + self.suffix + '.csv'), index=True)
                df_time_fit.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                'result', 'Time(fit)_' + self.suffix + '.csv'), index=True)
                df_time_inference.to_csv(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                      'result', 'Time(inference)_' + self.suffix + '.csv'), index=True)

        return results