import numpy as np
import ipdb as pdb
import itertools
import scipy.signal
from gym.spaces import Box, Discrete
import random

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.distributions.normal import Normal
from torch.distributions.categorical import Categorical
from torch.optim import Adam


def MLP(sizes, activation, output_activation=nn.Identity, **kwargs):
    layers = []
    for j in range(len(sizes)-1):
        act = activation if j < len(sizes)-2 else output_activation
        layers += [nn.Linear(sizes[j], sizes[j+1]), act()]
    return nn.Sequential(*layers)

def CNN(sizes, kernels, strides, paddings, activation, output_activation=nn.Identity, **kwargs):
    layers = []
    for j in range(len(sizes)-1):
        act = activation if j < len(sizes)-2 else output_activation
        layers += [nn.Conv2d(sizes[j], sizes[j+1], kernels[j], strides[j], paddings[j]), act()]
    return nn.Sequential(*layers)

class ParameterizedModel(nn.Module):
    """
        assumes parameterized state representation
        we may use a gaussian prediciton,
        but it degenrates without a kl hyperparam
        unlike the critic and the actor class, 
        the sizes argument does not include the dim of the state
        n_embedding is the number of embedding modules needed, = the number of discrete action spaces used as input
    """
    def __init__(self, env_fn, logger, n_embedding=1, **net_args):
        super().__init__()
        self.logger = logger.child("p")
        self.action_space=env_fn().action_space
        self.observation_space = env_fn().observation_space
        input_dim = net_args['sizes'][0]
        output_dim = net_args['sizes'][-1]
        self.n_embedding = n_embedding
        if isinstance(self.action_space, Discrete):
            self.action_embeddings = nn.ModuleList()
            for i in range(n_embedding):
                self.action_embeddings += [nn.Embedding(self.action_space.n,input_dim)]
        self.net = MLP(**net_args)
        self.state_head = nn.Linear(output_dim, self.observation_space.shape[0])
        self.reward_head = nn.Linear(output_dim, 1)
        self.done_head = nn.Linear(output_dim, 1)
        self.MSE = nn.MSELoss(reduction='none')
        self.BCE = nn.BCEWithLogitsLoss(reduction='none')

    def forward(self, s, a, r=None, s1=None, d=None):
        embedding = s
        if isinstance(self.action_space, Discrete):
            for i in range(self.n_embedding):
                embedding = embedding + self.action_embeddings[i](a[:, i])
        embedding = self.net(embedding)
        state = self.state_head(embedding)
        reward = self.reward_head(embedding).squeeze(1)
        
        if r is None: #inference
            with torch.no_grad():
                done = torch.sigmoid(self.done_head(embedding))
                done = torch.cat([1-done, done], dim = 1)
                done = Categorical(done).sample() # [b]
                return  reward, state, done
        else: # training
            done = self.done_head(embedding).squeeze(1)
            state_loss = self.MSE(state, s1).mean(dim = 1)
            state_var = self.MSE(s1, s1.mean(dim = 0, keepdim=True).expand(*s1.shape)).mean()
            
            reward_loss = self.MSE(reward, r)
            reward_var = self.MSE(reward, reward.mean(dim=0, keepdim=True).expand(*reward.shape)).mean()
            
            done_loss = self.BCE(done, d)
            done = done > 0

            done_true_positive = (done*d).mean()
            d = d.mean()
            
            self.logger.log(rel_state_loss=state_loss.mean()/state_var,
                            reward_loss=reward_loss,
                            reward_var=reward_var)
            self.logger.log(done_loss=done_loss,done_true_positive=done_true_positive, done=d, rolling=100)
            return state_loss+reward_loss+10*done_loss
        
class QCritic(nn.Module):
    """
    Dueling Q, currently only implemented for discrete action space
    if n_embedding > 0, assumes the action space needs embedding
    Notice that the output shape should be 1+action_space.n for discrete dueling Q
    
    n_embedding is the number of embedding modules needed, = the number of discrete action spaces used as input
    only used for decentralized multiagent, assumes the first action is local (consistent with gather() in utils)
    """
    def __init__(self, env_fn, n_embedding=0, **q_args):
        super().__init__()
        q_net = q_args['network']
        self.action_space=env_fn().action_space
        self.q = q_net(**q_args)
        self.n_embedding = n_embedding
        input_dim = q_args['sizes'][0]
        if not n_embedding is 0:
            self.action_embeddings = nn.ModuleList()
            for i in range(n_embedding):
                self.action_embeddings += [nn.Embedding(self.action_space.n,input_dim)]
       
    def forward(self, state, output_distribution, action=None):
        """
        action is only used for decentralized multiagent
        """
        if isinstance(self.action_space, Box):
            q = self.q(torch.cat([obs, action], dim=-1))
        else:
            if self.n_embedding > 0:
                # multiagent
                embedding = 0
                for i in range(self.n_embedding):
                    state = state + self.action_embeddings[i](action[:, 1+i])
                action = action[:, 0]
            q = self.q(state)
            while len(q.shape) > 2:
                q = q.squeeze(-1) # HW of size 1 if CNN
            # [b, a+1]
            v = q[:, -1:]
            q = q[:, :-1]
            q = q - q.mean(dim=1, keepdim=True) + v
            if output_distribution: 
                # q for all actions
                return q
            else:
                # q for a particular action
                q = torch.gather(input=q,dim=1,index=action.unsqueeze(-1))  
                return q.squeeze(dim=1)

class CategoricalActor(nn.Module):
    """ 
    always returns a distribution
    """
    def __init__(self, **net_args):
        super().__init__()
        self.softmax = nn.Softmax(dim=1)
        net_fn = net_args['network']
        self.network = net_fn(**net_args)
    
    def forward(self, obs):
        logit = self.network(obs)
        while len(logit.shape) > 2:
            logit = logit.squeeze(-1) # HW of size 1 if CNN
        return self.softmax(logit)
    
class RegressionActor(nn.Module):
    """
    determinsitc actor, used in DDPG and TD3
    """
    def __init__(self, **net_args):
        super().__init__()
        net_fn = net_args['network']
        self.network = net_fn(**net_args)
    
    def forward(self, obs):
        out = self.network(obs)
        while len(out.shape) > 2:
            out = out.squeeze(-1) # HW of size 1 if CNN
        return out
    