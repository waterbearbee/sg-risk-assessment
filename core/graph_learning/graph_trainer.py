import os, pdb, sys
sys.path.append(os.path.dirname(sys.path[0]))

import torch
import torch.nn.functional as F
import torch.optim as optim
import numpy as np
import scipy.sparse as sp
import pandas as pd

from core.graph_learning.models import base_model
from core.scene_graph.graph_process import SceneGraphExtractor
from core.graph_learning.utils import accuracy
from argparse import ArgumentParser
from pathlib import Path
from tqdm import tqdm
from core.graph_learning.models.gin import *
from core.graph_learning.models.gcn import *
from torch_geometric.data import Data, DataLoader

class Config:
    '''Argument Parser for script to train scenegraphs.'''
    def __init__(self, args):
        self.parser = ArgumentParser(description='The parameters for training the scene graph using GCN.')
        self.parser.add_argument('--input_path', type=str, default="../input/synthesis_data/lane-change/", help="Path to code directory.")
        self.parser.add_argument('--learning_rate', default=0.0001, type=float, help='The initial learning rate for GCN.')
        self.parser.add_argument('--seed', type=int, default=42, help='Random seed.')
        self.parser.add_argument('--epochs', type=int, default=200, help='Number of epochs to train.')
        self.parser.add_argument('--weight_decay', type=float, default=5e-4, help='Weight decay (L2 loss on parameters).')
        self.parser.add_argument('--hidden', type=int, default=200, help='Number of hidden units.')
        self.parser.add_argument('--dropout', type=float, default=0.5, help='Dropout rate (1 - keep probability).')
        self.parser.add_argument('--nclass', type=int, default=8, help="The number of classes for node.")
        self.parser.add_argument('--recursive', type=lambda x: (str(x).lower() == 'true'), default=True, help='Recursive loading scenegraphs')
        self.parser.add_argument('--batch_size', type=int, default=32, help='Number of graphs in a batch.')
        self.parser.add_argument('--device', type=str, default="cpu", help='The device to run on models (cuda or cpu) cpu in default.')
        self.parser.add_argument('--model', type=str, default="gcn", help="Model to be used intrinsically.")

        args_parsed = self.parser.parse_args(args)
        
        for arg_name in vars(args_parsed):
            self.__dict__[arg_name] = getattr(args_parsed, arg_name)

        self.input_base_dir = Path(self.input_path).resolve()


class GraphTrainer:

    def __init__(self, args):
        self.config = Config(args)
        np.random.seed(self.config.seed)
        torch.manual_seed(self.config.seed)

        self.preprocess_scenegraph_data() # reduced scenegraph extraction

    def preprocess_scenegraph_data(self):
        # load scene graph txts into memory 
        sge = SceneGraphExtractor()

        if not sge.is_cache_exists():
            if self.config.recursive:
                for sub_dir in tqdm([x for x in self.config.input_base_dir.iterdir() if x.is_dir()]):
                    data_source = sub_dir
                    sge.load(data_source)
            else:
                data_source = self.config.input_base_dir
                sge.load(data_source)

            self.training_graphs, self.training_labels, self.testing_graphs, self.testing_labels, self.feature_list = sge.to_dataset()
        else:
            self.training_graphs, self.training_labels, self.testing_graphs, self.testing_labels, self.feature_list = sge.read_cache()
        
        train_data_list = [Data(x=g.node_features, edge_index=g.edge_mat, y=torch.LongTensor([label])) for g, label in zip(self.training_graphs, self.training_labels)]
        self.train_loader = DataLoader(train_data_list, batch_size=32)
        test_data_list = [Data(x=g.node_features, edge_index=g.edge_mat, y=torch.LongTensor([label])) for g, label in zip(self.testing_graphs, self.testing_labels)]
        self.test_loader = DataLoader(test_data_list, batch_size=1)

        print("Number of Training Scene Graphs included: ", len(self.training_graphs))

    def build_model(self):
        if self.config.model == "gcn":
            self.model = GCN_Graph(len(self.feature_list), self.config.hidden, 2, self.config.dropout, "max").to(self.config.device)
        
        elif self.config.model == "gin":
            self.model = GIN(None, len(self.feature_list), 2).to(self.config.device)

        self.optimizer = optim.Adam(self.model.parameters(), lr=self.config.learning_rate, weight_decay=self.config.weight_decay)

    def train(self):

        for epoch_idx in tqdm(range(self.config.epochs)): # iterate through epoch
            acc_loss_train = 0
            
            for data in self.train_loader: # iterate through scenegraphs
                
                data.to(self.config.device)

                self.model.train()
                self.optimizer.zero_grad()
                               
                output = self.model.forward(data.x, data.edge_index, data.batch)
                    
                loss_train = nn.CrossEntropyLoss()(output, data.y)
                    
                loss_train.backward()

                self.optimizer.step()

                acc_loss_train += loss_train.detach().cpu().numpy()

            print('')
            print('Epoch: {:04d},'.format(epoch_idx), 'loss_train: {:.4f}'.format(acc_loss_train))
            print('')

    def predict(self):
        labels = []
        outputs = []
        
        for i in range(self.test_generator.number_of_batch): # iterate through scenegraphs
            
            data, label = next(self.test_generator)
            
            self.model.eval()
            output = self.model.forward(data)
            outputs.append(output)
            labels.append(label)
            acc_test = accuracy(output, torch.LongTensor(label))

            print('SceneGraph: {:04d}'.format(i), 'acc_test: {:.4f}'.format(acc_test.item()))
        outputs = torch.cat(outputs).reshape(-1,2).detach()
        if self.config.device == "cuda":
            # move tensor back to cpu
            outputs = outputs.cpu()
        return outputs, np.array(labels).flatten()
        
        