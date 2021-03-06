import theano
import theano.tensor as T
import numpy as np
import os
import json
import socket
import cPickle as pickle
import argparse
import time
import datetime
import code

from layers.dynamic_filter_layer import DynamicFilterLayer

# lasagne
import lasagne
from lasagne.layers import EmbeddingLayer, DenseLayer, ReshapeLayer, ConcatLayer, Gate, LSTMLayer, DropoutLayer, SliceLayer, InputLayer, ElemwiseMergeLayer, NonlinearityLayer, FeaturePoolLayer, DimshuffleLayer, Upscale2DLayer, ElemwiseSumLayer, BiasLayer
from lasagne.layers.dnn import Conv2DDNNLayer as ConvLayer
from lasagne.layers.dnn import MaxPool2DDNNLayer as PoolLayer
from lasagne.updates import rmsprop
#from lasagne.regularization import 
from lasagne.nonlinearities import softmax, identity, sigmoid, tanh, rectify, leaky_rectify
from lasagne.init import Uniform, Constant, Normal, HeUniform
from lasagne.utils import create_param

class Model(object):
    """ model initialization """
    def __init__(self, npx=64, batch_size=16, input_seqlen=3, target_seqlen=3, buffer_len=1, dynamic_filter_size=(9,9), refinement_network=False, dynamic_bias=False):
        self.npx = npx
        self.batch_size = batch_size
        self.input_seqlen = input_seqlen
        self.target_seqlen = target_seqlen
        self.nInputs = buffer_len
        self.dynamic_filter_size = dynamic_filter_size
        self.refinement_network = refinement_network
        self.dynamic_bias = dynamic_bias

    def build_model(self, input_batch):

        ## initialize shared parameters
        Ws = []
        bs = []
        nLayersWithParams = 13
        if self.refinement_network:
            nLayersWithParams = nLayersWithParams + 4
        for i in range(nLayersWithParams):
            W = HeUniform()
            Ws.append(W)
            b = Constant(0.0)
            bs.append(b)
        hidden_state = InputLayer(input_var=np.zeros((self.batch_size, 128, self.npx/2, self.npx/2), dtype=np.float32), shape=(self.batch_size, 128, self.npx/2, self.npx/2))

        ## get inputs
        inputs = InputLayer(input_var=input_batch, shape=(None, self.input_seqlen, self.npx, self.npx))
        # inputs = InputLayer(input_var=input_batch, shape=(None, 1, self.npx, self.npx, self.input_seqlen))
        # inputs = DimshuffleLayer(inputs, (0, 4, 2, 3, 1))
        outputs = []
        for i in range(self.input_seqlen - self.nInputs + self.target_seqlen):
            input = SliceLayer(inputs, indices=slice(0,self.nInputs), axis=1)
            output, hidden_state, filters = self.predict(input, hidden_state, Ws, bs)
            ## FIFO operation.
            inputs = SliceLayer(inputs, indices=slice(1, None), axis=1)

            if i == self.input_seqlen - self.nInputs:
                filtersToVisualize = filters
            if i >= self.input_seqlen - self.nInputs:
                inputs = ConcatLayer([inputs, output], axis=1)
                outputs.append(output)


        return output, outputs, filtersToVisualize

    def predict(self, input, hidden_state, Ws, bs):
                
        npx = self.npx # image size                
        nc = self.input_seqlen
        filter_size = self.dynamic_filter_size[0]
        f = 0

        ###############################
        #  filter-generating network  #
        ###############################
        ## encoder
        output = ConvLayer(input, num_filters=64, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify, untie_biases=True); Ws[f] = output.W; bs[f] = output.b; f = f+1
        output = ConvLayer(output, num_filters=64, filter_size=(3,3), stride=(2,2), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
        output = ConvLayer(output, num_filters=128, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
        # output = ConvLayer(output, num_filters=64, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1

        ## mid
        output = ConvLayer(output, num_filters=128, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1

        hidden = ConvLayer(hidden_state, num_filters=128, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = hidden.W; bs[f] = hidden.b; f = f+1
        hidden = ConvLayer(hidden, num_filters=128, filter_size=(3, 3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = hidden.W; bs[f] = hidden.b; f = f+1
        output = ElemwiseSumLayer([output, hidden])
        hidden_state = output

        ## decoder
        # output = ConvLayer(output, num_filters=64, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
        output = ConvLayer(output, num_filters=128, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
        output = Upscale2DLayer(output, scale_factor = 2)
        output = ConvLayer(output, num_filters=64, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
        output = ConvLayer(output, num_filters=128, filter_size=(3,3), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1

        # output = ConvLayer(output, num_filters=128, filter_size=(1,1), stride=(1,1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1

        ## filter-generating layers
        l_filter = ConvLayer(output, num_filters=filter_size**2 + self.dynamic_bias, filter_size=(1,1), stride=(1,1), pad=(0,0), W=Ws[f], b=bs[f], nonlinearity=identity); Ws[f] = l_filter.W; bs[f] = l_filter.b; f = f+1

        #########################
        #  transformer network  #
        #########################
        ## get inputs
        output = SliceLayer(input, indices=slice(self.nInputs-1, self.nInputs), axis=1) # select the last (most recent) frame from the inputs

        ## add a bias
        if self.dynamic_bias:
            filters_biases = SliceLayer(l_filter, indices=slice(filter_size ** 2, filter_size ** 2 + 1), axis=1)
            output = ConcatLayer([output, filters_biases])
            output = FeaturePoolLayer(output, pool_size=2, pool_function=theano.tensor.sum)

        ## dynamic convolution
        filters = SliceLayer(l_filter, indices=slice(0, filter_size ** 2), axis=1)

        # filters = FeaturePoolLayer(filters, pool_size=9*9, pool_function=theano.tensor.nnet.softmax)
        filters = DimshuffleLayer(filters, (0, 2, 3, 1))
        filters = ReshapeLayer(filters, shape=(-1, filter_size ** 2))
        filters = NonlinearityLayer(filters, nonlinearity=softmax)
        filters = ReshapeLayer(filters, shape=(-1, npx, npx, filter_size ** 2))
        filters = DimshuffleLayer(filters, (0, 3, 1, 2))

        output_dynconv = DynamicFilterLayer([output, filters], filter_size=(filter_size,filter_size,1), pad=(filter_size//2, filter_size//2))

        ########################
        #  refinement network  #
        ########################
        if self.refinement_network:
            output = ConcatLayer([output_dynconv, input])
            output = ConvLayer(output, num_filters=32, filter_size=(3, 3), stride=(1, 1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
            output = ConvLayer(output, num_filters=64, filter_size=(3, 3), stride=(1, 1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
            output = ConvLayer(output, num_filters=32, filter_size=(3, 3), stride=(1, 1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
            output = ConvLayer(output, num_filters=1, filter_size=(3, 3), stride=(1, 1), pad='same', W=Ws[f], b=bs[f], nonlinearity=leaky_rectify); Ws[f] = output.W; bs[f] = output.b; f = f+1
            output = ElemwiseSumLayer([output_dynconv, output]) # this is a residual connection
        else:
            output = output_dynconv
                        
        return output, hidden_state, filters
