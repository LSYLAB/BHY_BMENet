"""
AUTOENCODER WITH ARCHTECTURE FROM VERSION 2
"""
from typing import Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from models.distributions import DiagonalGaussianDistribution


@torch.jit.script
def swish(x):
    return x * torch.sigmoid(x)


def Normalize(in_channels):
    return nn.GroupNorm(num_groups=32, num_channels=in_channels, eps=1e-6, affine=True)


class Upsample(nn.Module):
    def __init__(self, in_channels):
        super().__init__()
        self.conv = nn.Conv3d(
            in_channels, in_channels, kernel_size=3, stride=1, padding=1
        )

    def forward(self, x):
        x = F.interpolate(x, scale_factor=2.0, mode="nearest")
        x = self.conv(x)
        return x


class Downsample(nn.Module):
    def __init__(self, in_channels):
        super().__init__()
        self.conv = nn.Conv3d(
            in_channels, in_channels, kernel_size=3, stride=2, padding=0
        )

    def forward(self, x):
        pad = (0, 1, 0, 1, 0, 1)
        x = nn.functional.pad(x, pad, mode="constant", value=0)
        x = self.conv(x)
        return x


class ResBlock(nn.Module):
    def __init__(self, in_channels, out_channels=None):
        super().__init__()
        self.in_channels = in_channels
        self.out_channels = in_channels if out_channels is None else out_channels
        self.norm1 = Normalize(in_channels)
        self.conv1 = nn.Conv3d(
            in_channels, out_channels, kernel_size=3, stride=1, padding=1
        )
        self.norm2 = Normalize(out_channels)
        self.conv2 = nn.Conv3d(
            out_channels, out_channels, kernel_size=3, stride=1, padding=1
        )

        if self.in_channels != self.out_channels:
            self.nin_shortcut = nn.Conv3d(
                in_channels, out_channels, kernel_size=1, stride=1, padding=0
            )

    def forward(self, x):
        h = x
        h = self.norm1(h)
        h = F.silu(h)
        h = self.conv1(h)

        h = self.norm2(h)
        h = F.silu(h)
        h = self.conv2(h)

        if self.in_channels != self.out_channels:
            x = self.nin_shortcut(x)

        return x + h


class Encoder(nn.Module):
    def __init__(
        self,
        in_channels: 64, # 64
        n_channels: 1,  # 1
        z_channels: 3,   # 3
        ch_mult: [1,2,2,2], # [1,2,2,2]
        num_res_blocks: 2, # 2
        resolution: [256],
        attn_resolutions: [],
        **ignorekwargs,
    ) -> None:
        super().__init__()
        self.in_channels = in_channels
        self.n_channels = n_channels
        self.num_resolutions = len(ch_mult)
        self.num_res_blocks = num_res_blocks
        self.resolution = resolution
        self.attn_resolutions = attn_resolutions

        curr_res = resolution
        in_ch_mult = (1,) + tuple(ch_mult)

        blocks = []
        # initial convolution
        blocks.append(
            nn.Conv3d(in_channels, n_channels, kernel_size=3, stride=1, padding=1)
        )

        # residual and downsampling blocks, with attention on smaller res (16x16)
        for i in range(self.num_resolutions):
            block_in_ch = n_channels * in_ch_mult[i]
            block_out_ch = n_channels * ch_mult[i]
            for _ in range(self.num_res_blocks):
                blocks.append(ResBlock(block_in_ch, block_out_ch))
                block_in_ch = block_out_ch

            if i != self.num_resolutions - 1:
                blocks.append(Downsample(block_in_ch))
                curr_res = tuple(ti // 2 for ti in curr_res)

        # normalise and convert to latent size
        blocks.append(Normalize(block_in_ch))
        blocks.append(
            nn.Conv3d(block_in_ch, z_channels, kernel_size=3, stride=1, padding=1)
        )

        self.blocks = nn.ModuleList(blocks)

    def forward(self, x):
        for block in self.blocks:
            x = block(x)
        return x


class Decoder(nn.Module):
    def __init__(
        self,
        n_channels: 64, # 64
        z_channels: 3,  # 3
        out_channels: 1, # 1
        ch_mult: [1,2,2,2], # [1,2,2,2]
        num_res_blocks: 2, # 2
        resolution: [256],
        attn_resolutions: [],
        **ignorekwargs,
    ) -> None:
        super().__init__()
        self.n_channels = n_channels
        self.z_channels = z_channels
        self.out_channels = out_channels
        self.ch_mult = ch_mult
        self.num_resolutions = len(ch_mult)
        self.num_res_blocks = num_res_blocks
        self.resolution = resolution
        self.attn_resolutions = attn_resolutions

        block_in_ch = n_channels * self.ch_mult[-1]
        curr_res = tuple(ti // 2 ** (self.num_resolutions - 1) for ti in resolution)

        blocks = []
        # initial conv
        blocks.append(
            nn.Conv3d(z_channels, block_in_ch, kernel_size=3, stride=1, padding=1)
        )

        for i in reversed(range(self.num_resolutions)):
            block_out_ch = n_channels * self.ch_mult[i]

            for _ in range(self.num_res_blocks):
                blocks.append(ResBlock(block_in_ch, block_out_ch))
                block_in_ch = block_out_ch

            if i != 0:
                blocks.append(Upsample(block_in_ch))
                curr_res = tuple(ti * 2 for ti in curr_res)

        blocks.append(Normalize(block_in_ch))
        blocks.append(
            nn.Conv3d(block_in_ch, out_channels, kernel_size=3, stride=1, padding=1)
        )

        self.blocks = nn.ModuleList(blocks)

    def forward(self, x):
        for block in self.blocks:
            x = block(x)
        return x


class AutoencoderKL(nn.Module):
    def __init__(self, embed_dim:3) -> None:
        super().__init__()
        self.encoder = Encoder(in_channels= 1, # 64
        n_channels= 64,  # 1
        z_channels= 3,   # 3
        ch_mult= [1,2,2,2], # [1,2,2,2]
        num_res_blocks= 2, # 2
        resolution= [256],
        attn_resolutions= [])

        self.decoder = Decoder(n_channels = 64, # 64
        z_channels=3,  # 3
        out_channels= 1, # 1
        ch_mult=[1,2,2,2], # [1,2,2,2]
        num_res_blocks= 2, # 2
        resolution= [256],
        attn_resolutions= [])

        self.quant_conv_mu = torch.nn.Conv3d(3, embed_dim, 1)
        self.quant_conv_log_sigma = torch.nn.Conv3d(3, embed_dim, 1)
        self.post_quant_conv = torch.nn.Conv3d(embed_dim, 3, 1)
        
        self.embed_dim = embed_dim

    def decode(self, z):
        z = self.post_quant_conv(z)
        dec = self.decoder(z)
        return dec
    
    # def encode(self, z):
    #     dec = self.encoder(z)
    #     return dec

    def encode(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        """
        Forwards an image through the spatial encoder, obtaining the latent mean and sigma representations.

        Args:
            x: BxCx[SPATIAL DIMS] tensor

        """
        h = self.encoder(x)

        z_mu = self.quant_conv_mu(h)
        # z_log_var = c
        # z_log_var = torch.clamp(z_log_var, -30.0, 20.0)
        # z_sigma = torch.exp(z_log_var / 2)

        return z_mu
    
    def sampling(self, z_mu: torch.Tensor, z_sigma: torch.Tensor) -> torch.Tensor:
        """
        From the mean and sigma representations resulting of encoding an image through the latent space,
        obtains a noise sample resulting from sampling gaussian noise, multiplying by the variance (sigma) and
        adding the mean.

        Args:
            z_mu: Bx[Z_CHANNELS]x[LATENT SPACE SIZE] mean vector obtained by the encoder when you encode an image
            z_sigma: Bx[Z_CHANNELS]x[LATENT SPACE SIZE] variance vector obtained by the encoder when you encode an image

        Returns:
            sample of shape Bx[Z_CHANNELS]x[LATENT SPACE SIZE]
        """
        eps = torch.randn_like(z_sigma)
        z_vae = z_mu + eps * z_sigma
        return z_vae
    

    def reconstruct_ldm_outputs(self, z):
        x_hat = self.decode(z)
        return x_hat


class OnlyDecoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.decoder = None
        self.post_quant_conv = None

    def decode(self, z):
        z = self.post_quant_conv(z)
        dec = self.decoder(z)
        return dec

    def reconstruct_ldm_outputs(self, z):
        x_hat = self.decode(z)
        return x_hat
    
class OnlyEncoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.encoder = Encoder(in_channels= 1, # 64
        n_channels= 64,  # 1
        z_channels= 3,   # 3
        ch_mult= [1,2,2,2], # [1,2,2,2]
        num_res_blocks= 2, # 2
        resolution= [256],
        attn_resolutions= [])
        self.quant_conv_mu = torch.nn.Conv3d(3, embed_dim, 1)

    def encode(self, z):
        h = self.encoder(z)

        z_mu = self.quant_conv_mu(h)
        return z_mu

