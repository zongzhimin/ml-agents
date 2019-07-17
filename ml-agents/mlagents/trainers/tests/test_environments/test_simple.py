import yaml
import math
import tempfile
from typing import Any, Callable, Dict, Optional, List


from mlagents.trainers.trainer_controller import TrainerController
from mlagents.envs.base_unity_environment import BaseUnityEnvironment
from mlagents.envs.env_manager import EnvManager, StepInfo
from mlagents.envs.timers import timed
from mlagents.envs import ActionInfo, BrainInfo, AllBrainInfo, BrainParameters
from mlagents.envs.communicator_objects import AgentInfoProto


BRAIN_NAME = __name__
OBS_SIZE = 1
STEP_SIZE = 0.1

TIME_PENALTY = 0.001
MIN_STEPS = int(1.0 / STEP_SIZE) + 1
SUCCESS_REWARD = 1.0 + MIN_STEPS * TIME_PENALTY


def clamp(x, min_val, max_val):
    return max(min_val, min(x, max_val))


class Simple1DEnvironment(BaseUnityEnvironment):
    """
    Very simple "game" - the agent has a position on [-1, 1], gets a reward of 1 if it reaches 1, and a reward of -1 if
    it reaches -1. The position is incremented by the action amount (clamped to [-step_size, step_size]).
    """

    def __init__(self):
        self._brains: Dict[str, BrainParameters] = {}
        self._brains[BRAIN_NAME] = BrainParameters(
            brain_name=BRAIN_NAME,
            vector_observation_space_size=OBS_SIZE,
            num_stacked_vector_observations=1,
            camera_resolutions=[],
            vector_action_space_size=[1],
            vector_action_descriptions=["moveDirection"],
            vector_action_space_type=1,  # "continuous"
        )

        # state
        self.position = 0.0
        self.step_count = 0

    def step(
        self,
        vector_action: Dict[str, Any] = None,
        memory: Dict[str, Any] = None,
        text_action: Dict[str, Any] = None,
        value: Dict[str, Any] = None,
    ) -> AllBrainInfo:
        assert vector_action is not None

        delta = vector_action[BRAIN_NAME][0][0]
        delta = clamp(delta, -STEP_SIZE, STEP_SIZE)
        self.position += delta
        self.position = clamp(self.position, -1, 1)
        self.step_count += 1
        done = self.position >= 1.0 or self.position <= -1.0
        if done:
            reward = SUCCESS_REWARD * self.position
        else:
            reward = -TIME_PENALTY

        agent_info = AgentInfoProto(
            stacked_vector_observation=[self.position] * OBS_SIZE,
            reward=reward,
            done=done,
        )

        if done:
            self._reset_agent()

        return {
            BRAIN_NAME: BrainInfo.from_agent_proto(
                0, [agent_info], self._brains[BRAIN_NAME]
            )
        }

    def _reset_agent(self):
        self.position = 0.0
        self.step_count = 0

    def reset(
        self,
        config: Dict[str, float] = None,
        train_mode: bool = True,
        custom_reset_parameters: Any = None,
    ) -> AllBrainInfo:  # type: ignore
        self._reset_agent()

        agent_info = AgentInfoProto(
            stacked_vector_observation=[self.position] * OBS_SIZE,
            done=False,
            max_step_reached=False,
        )
        return {
            BRAIN_NAME: BrainInfo.from_agent_proto(
                0, [agent_info], self._brains[BRAIN_NAME]
            )
        }

    @property
    def global_done(self):
        return False

    @property
    def external_brains(self) -> Dict[str, BrainParameters]:
        return self._brains

    @property
    def reset_parameters(self) -> Dict[str, str]:
        return {}

    def close(self):
        pass


class EnvContext:
    def __init__(self, env: BaseUnityEnvironment):
        self.env = env
        self.previous_step: StepInfo = StepInfo(None, {}, None)
        self.previous_all_action_info: Dict[str, ActionInfo] = {}


# Copied from SubprocessEnvManager and removed the subprocess part
class LocalEnvManager(EnvManager):
    def __init__(self, env: BaseUnityEnvironment):
        super().__init__()
        self.env = env
        self.previous_step: StepInfo = StepInfo(None, {}, None)
        self.previous_all_action_info: Dict[str, ActionInfo] = {}

    def get_last_steps(self):
        return [self.previous_step]

    def step(self) -> List[StepInfo]:

        all_action_info = self._take_step(self.previous_step)
        self.previous_all_action_info = all_action_info

        if self.env.global_done:
            all_brain_info = self.env.reset()
        else:
            actions = {}
            memories = {}
            texts = {}
            values = {}
            for brain_name, action_info in all_action_info.items():
                actions[brain_name] = action_info.action
                memories[brain_name] = action_info.memory
                texts[brain_name] = action_info.text
                values[brain_name] = action_info.value
            all_brain_info = self.env.step(actions, memories, texts, values)
        step_brain_info = all_brain_info

        step_info = StepInfo(
            self.previous_step.current_all_brain_info,
            step_brain_info,
            self.previous_all_action_info,
        )
        self.previous_step = step_info
        return [step_info]

    def reset(
        self,
        config: Dict[str, float] = None,
        train_mode: bool = True,
        custom_reset_parameters: Any = None,
    ) -> List[StepInfo]:  # type: ignore
        all_brain_info = self.env.reset(
            config=config,
            train_mode=train_mode,
            custom_reset_parameters=custom_reset_parameters,
        )
        self.previous_step = StepInfo(None, all_brain_info, None)
        return [self.previous_step]

    @property
    def external_brains(self) -> Dict[str, BrainParameters]:
        return self.env.external_brains

    @property
    def reset_parameters(self) -> Dict[str, float]:
        return self.env.reset_parameters

    def close(self):
        self.env.close()

    @timed
    def _take_step(self, last_step: StepInfo) -> Dict[str, ActionInfo]:
        all_action_info: Dict[str, ActionInfo] = {}
        for brain_name, brain_info in last_step.current_all_brain_info.items():
            all_action_info[brain_name] = self.policies[brain_name].get_action(
                brain_info
            )
        return all_action_info


config = """
default:
    trainer: ppo
    batch_size: 16
    beta: 5.0e-3
    buffer_size: 64
    epsilon: 0.2
    hidden_units: 128
    lambd: 0.95
    learning_rate: 5.0e-3
    max_steps: 2500
    memory_size: 256
    normalize: false
    num_epoch: 3
    num_layers: 2
    time_horizon: 64
    sequence_length: 64
    summary_freq: 500
    use_recurrent: false
    reward_signals: 
        extrinsic:
            strength: 1.0
            gamma: 0.99

"""


def test_simple():
    # Create controller and begin training.
    with tempfile.TemporaryDirectory() as dir:
        run_id = "id"
        save_freq = 99999
        tc = TrainerController(
            dir,
            dir,
            run_id,
            save_freq,
            meta_curriculum=None,
            load=False,
            train=True,
            keep_checkpoints=1,
            lesson=None,
            training_seed=1337,
            fast_simulation=True,
        )

        # Begin training
        env = Simple1DEnvironment()
        env_manager = LocalEnvManager(env)
        trainer_config = yaml.safe_load(config)
        tc.start_learning(env_manager, trainer_config)

        for brain_name, mean_reward in tc._get_measure_vals().items():
            assert not math.isnan(mean_reward)
            assert mean_reward > 0.99
