from __future__ import (absolute_import, unicode_literals)

import logging
log = logging.getLogger('sknn')


import numpy as np
import theano
import sklearn.base

from pylearn2.datasets import DenseDesignMatrix
from pylearn2.training_algorithms import sgd, bgd
from pylearn2.models import mlp, maxout
from pylearn2.costs.mlp.dropout import Dropout
from pylearn2.training_algorithms.learning_rule import RMSProp, Momentum



class NeuralNetwork(sklearn.base.BaseEstimator):
    """
    A wrapper for PyLearn2 compatible with scikit-learn.

    Parameters
    ----------
    layers : list of tuples
        An iterable sequence of each layer each as a tuple: first with an
        activation type and then optional parameters such as the number of
        units.

    random_state : int
        Seed for the initialization of the neural network parameters (e.g.
        weights and biases).  This is fully deterministic.

    learning_rule : str
        Name of the learning rule used during stochastic gradient descent,
        one of ('default', 'momentum', 'rmsprop') at the moment.    

    learning_rate : float
        Real number indicating the default/starting rate of adjustment for
        the weights during gradient descent.  Different learning rules may
        take this into account differently.

    learning_momentum : float
        Real number indicating the momentum factor to be used for the
        learning rule 'momentum'.

    batch_size : int
        Number of training samples to group together when performing stochastic
        gradient descent.  By default each sample is treated on its own.

    n_iter : int
        The number of iterations of gradient descent to perform on the
        neural network's weights when training with fit().

    dropout : bool
        Whether to use drop-out training for the inputs (jittering) and the
        hidden layers, for each training example.
    """

    def __init__(
            self,
            layers,
            random_state=None,
            learning_rule='default',
            learning_rate=0.01,
            learning_momentum=0.9,
            batch_size=1,
            n_iter=1,
            dropout=False):

        self.layers = layers
        self.seed = random_state

        self.unit_counts = None
        self.mlp = None
        self.ds = None
        self.trainer = None
        self.f = None

        self.cost = "Dropout" if dropout else None
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        self.n_iter = n_iter

        if learning_rule == 'default':
            self.learning_rule = None
        elif learning_rule == 'momentum':
            self.learning_rule = Momentum(learning_momentum)
        elif learning_rule == 'rmsprop':
            self.learning_rule = RMSProp()
        else:
            raise NotImplementedError(
                "Learning rule type `%s` is not supported." % learning_rule)

    def _create_trainer(self):
        sgd.log.setLevel(logging.WARNING)

        if self.cost == "Dropout":
            first_hidden_name = "Hidden_0_"+self.layers[0][0]
            self.cost = Dropout(
                input_include_probs={first_hidden_name: 1.0},
                input_scales={first_hidden_name: 1.})

        return sgd.SGD(
            learning_rate=self.learning_rate,
            cost=self.cost,
            batch_size=1,
            learning_rule=self.learning_rule)

    def _create_hidden_layer(self, name, args, irange=0.1):
        activation_type = args[0]
        if activation_type == "RectifiedLinear":
            return mlp.RectifiedLinear(
                dim=args[1],
                layer_name=name,
                irange=irange)

        if activation_type == "Sigmoid":
            return mlp.Sigmoid(
                dim=args[1],
                layer_name=name,
                irange=irange)

        if activation_type == "Tanh":
            return mlp.Tanh(
                dim=args[1],
                layer_name=name,
                irange=irange)

        if activation_type == "Maxout":
            return maxout.Maxout(
                num_units=args[1],
                num_pieces=args[2],
                layer_name=name,
                irange=irange)

        raise NotImplementedError(
            "Hidden layer type `%s` is not implemented." % activation_type)

    def _create_output_layer(self, name, args):
        activation_type = args[0]
        if activation_type == "Linear":
            return mlp.Linear(
                dim=args[1],
                layer_name=name,
                irange=0.00001)

        if activation_type == "LinearGaussian":
            return mlp.LinearGaussian(
                init_beta=0.1,
                min_beta=0.001,
                max_beta=1000,
                beta_lr_scale=None,
                dim=args[1],
                layer_name=name,
                irange=0.1)

        raise NotImplementedError(
            "Output layer type `%s` is not implemented." % name)

    def _create_mlp(self, X, y):
        # Calculate and store all layer sizes.
        self.unit_counts = [X.shape[1]]
        for layer in self.layers[:-1]:
            self.unit_counts += [layer[1]]
        self.unit_counts += [y.shape[1]]

        log.debug("Units per layer: %r.", self.unit_counts)

        mlp_layers = []
        for i, layer in enumerate(self.layers[:-1]):
            fan_in = self.unit_counts[i] + 1
            fan_out = self.unit_counts[i + 1]
            lim = np.sqrt(6) / (np.sqrt(fan_in + fan_out))

            layer_name = "Hidden_%i_%s" % (i, layer[0])
            hidden_layer = self._create_hidden_layer(layer_name, layer, irange=lim)
            mlp_layers.append(hidden_layer)

        output_layer_info = list(self.layers[-1])
        output_layer_info.append(self.unit_counts[-1])

        output_layer_name = "Output_%s" % output_layer_info[0]
        output_layer = self._create_output_layer(output_layer_name, output_layer_info)
        mlp_layers.append(output_layer)

        return mlp.MLP(mlp_layers, nvis=self.unit_counts[0], seed=self.seed)

    def initialize(self, X, y):
        assert not self.initialized,\
            "This neural network has already been initialized."

        log.info(
            "Initializing neural network with %i layers.",
            len(self.layers))

        if self.mlp is None:
            self.mlp = self._create_mlp(X, y)

        self.ds = DenseDesignMatrix(X=X, y=y)
        self.trainer = self._create_trainer()
        self.trainer.setup(self.mlp, self.ds)
        inputs = self.mlp.get_input_space().make_theano_batch()
        self.f = theano.function([inputs], self.mlp.fprop(inputs))

    @property
    def initialized(self):
        """Check if the neural network was setup already.
        """
        return not (self.ds is None or self.trainer is None or self.f is None)

    def fit(self, X, y):
        """Fit the neural network to the given data.

        Parameters
        ----------
        X : array-like, shape = [n_samples, n_inputs]
            Training vectors as real numbers, where n_samples is the number of
            samples and n_inputs is the number of input features.

        y : array-like, shape = [n_samples, n_outputs]
            Target values as real numbers, either as regression targets or
            label probabilities for classification.

        Returns
        -------
        self : object
            Returns this instance.
        """
        assert X.shape[0] == y.shape[0],\
            "Expecting same number of input and output samples."

        if not self.initialized:
            self.initialize(X, y)

        self.ds.X, self.ds.y = X, y
        for _ in range(self.n_iter):
            self.trainer.train(dataset=self.ds)

        return self

    def predict(self, X):
        """Calculate predictions for specified inputs.

        Parameters
        ----------
        X : array-like of shape = [n_samples, n_inputs]
            The input samples as real numbers.

        Returns
        -------
        y : array of shape = [n_samples, n_outputs]
            The predicted values as real numbers.
        """

        if not self.initialized:
            assert self.unit_counts is not None,\
                "The neural network has not been trained."
            y = np.zeros((X.shape[0], self.unit_counts[-1]))
            self.initialize(X, y)

        return self.f(X)

    def __getstate__(self):
        assert self.mlp is not None,\
            "The neural network has not been initialized."

        d = self.__dict__.copy()
        for k in ['ds', 'f', 'trainer']:
            if k in d:
                del d[k]
        return d

    def __setstate__(self, d):
        self.__dict__.update(d)

        for k in ['ds', 'f', 'trainer']:
            setattr(self, k, None)


class SimpleNeuralRegressor(NeuralNetwork):
    """Simplified version of a `NeuralNetwork` that operates on a 1D output
    array, like most other forms of machine learning.
    """

    def fit(self, X, y, **kwargs):
        assert len(y.shape) == 1, "Expecting 1D input only."

        ys = y.reshape((y.size, 1))
        return super(SimpleNeuralRegressor, self).fit(X, ys, **kwargs)