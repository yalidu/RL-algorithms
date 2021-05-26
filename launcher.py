import os
import torch

from algorithms.config.CACC_MBPO_factorized import main
#from algorithms.envs.CACC import CACC_slowdown as env_fn
from algorithms.envs.CACC import CACC_catchup as env_fn


"""
from algorithms.config.ATSC_MBPO import main
from algorithms.envs.ATSC import ATSCGrid as env_fn
"""


os.environ['CUDA_VISIBLE_DEVICES']='5'
os.environ['RAY_OBJECT_STORE_ALLOW_SLOW_STORAGE']='1'
device = 'cuda'
name = 'factorized'

init_checkpoint = None
print(f"num GPUs: {torch.cuda.device_count()}")
main(env_fn=env_fn, init_checkpoint=None, debug=False, test=False, seed=None, name=name, device=device)
# device ='cpu' or 'cuda', no need to set ordinal
