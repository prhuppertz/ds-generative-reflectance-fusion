import torch
from torch.utils.data import DataLoader
import torch.nn.functional as F

from src.rsgan import build_model, build_dataset
from .experiment import Experiment
from .utils import stack_optical_and_sar
from ..experiments import EXPERIMENTS


@EXPERIMENTS.register('dummy_cloud_removal')
class DummyCloudRemoval(Experiment):
    """Dummy setup to train and evaluate an autoencoder at cloud removal

    Args:
        autoencoder (AutoEncoder)
        dataset (ToyCloudRemovalDataset)
        split (list[float]): dataset split ratios in [0, 1] as [train, val]
            or [train, val, test]
        seed (int): random seed (default: None)
    """
    def __init__(self, autoencoder, dataset, split, seed=None):
        super().__init__(model=autoencoder,
                         dataset=dataset,
                         split=split,
                         seed=seed)

    def forward(self, x):
        return self.model(x)

    def train_dataloader(self):
        """Implements LightningModule train loader building method
        """
        loader = DataLoader(dataset=self.train_set,
                            batch_size=16,
                            collate_fn=stack_optical_and_sar)
        return loader

    def val_dataloader(self):
        """Implements LightningModule validation loader building method
        """
        loader = DataLoader(dataset=self.val_set,
                            batch_size=16,
                            collate_fn=stack_optical_and_sar)
        return loader

    def configure_optimizers(self):
        """Implements LightningModule optimizer and learning rate scheduler
        building method
        """
        return torch.optim.Adam(self.parameters(), lr=1e-3)

    def training_step(self, batch, batch_idx):
        """Implements LightningModule training logic

        Args:
            batch (tuple[torch.Tensor]): source, target pairs batch
            batch_idx (int)

        Returns:
            type: dict
        """
        # Unfold batch
        source, target = batch
        # Forward pass
        output = self(source)

        # Compute loss and metrics
        loss = self._compute_loss(output, target)
        mse = self._compute_metrics(output, target)

        # Setup logs dictionnary
        tensorboard_logs = {'Loss/train': loss, 'MSE/train': mse}
        output = {'loss': loss,
                  'progress_bar': tensorboard_logs,
                  'log': tensorboard_logs}
        return output

    def on_epoch_end(self):
        """Implements LightningModule end of epoch operations
        """
        # Store into logger batch of images for visualization
        if not hasattr(self.logger, '_logging_images'):
            val_loader = self.val_dataloader()
            source, target = iter(val_loader).next()
            self.logger._logging_images = (source, target)

        # Compute generated samples out of logging images
        source, target = self.logger._logging_images
        with torch.no_grad():
            output = self(source)

        # Log fake-RGB version for visualization
        self.logger.log_images(output[:, :3], tag='Generated', step=self.current_epoch)
        self.logger.log_images(target[:, :3], tag='Target', step=self.current_epoch)

    def validation_step(self, batch, batch_idx):
        """Implements LightningModule validation logic

        Args:
            batch (tuple[torch.Tensor]): source, target pairs batch
            batch_idx (int)

        Returns:
            type: dict
        """
        # Unfold batch
        source, target = batch

        # Forward pass
        output = self(source)

        # Compute loss and metrics
        loss = self._compute_loss(output, target)
        mse = self._compute_metrics(output, target)
        return {'loss': loss, 'mse': mse}

    def validation_epoch_end(self, outputs):
        """LightningModule validation epoch end hook

        Args:
            outputs (list[dict]): list of validation steps outputs

        Returns:
            type: dict
        """
        # Average loss and metrics
        loss = torch.stack([x['loss'] for x in outputs]).mean()
        mse = torch.stack([x['mse'] for x in outputs]).mean()

        # Make tensorboard logs and return
        tensorboard_logs = {'Loss/val': loss, 'MSE/val': mse}
        return {'val_loss': loss, 'log': tensorboard_logs}

    def _compute_loss(self, output, target):
        loss = F.smooth_l1_loss(output, target)
        return loss

    def _compute_metrics(self, output, target):
        mse = F.mse_loss(output, target)
        return mse

    @classmethod
    def build(cls, cfg):
        exp_kwargs = {'autoencoder': build_model(cfg['model']),
                      'dataset': build_dataset(cfg['dataset']),
                      'split': list(cfg['dataset']['split'].values()),
                      'seed': cfg['experiment']['seed']}
        return cls(**exp_kwargs)