import numpy as np
import torch.nn as nn


class Conv2d(nn.Module):
    """Conv2d + BatchNorm + Dropout + ReLU
    Args:
        in_channels (int): Number of channels in the input image
        out_channels (int): Number of channels produced by the convolution
        kernel_size (int or tuple): Size of the convolving kernel
        stride (int or tuple, optional): Stride of the convolution. Default: 1
        padding (int or tuple, optional): Zero-padding added to both sides of the input. Default: 0
        dilation (int or tuple, optional): Spacing between kernel elements. Default: 1
        relu (bool, str): if True, uses ReLU - if 'learn', uses PReLU
        leak (float): if > 0 and relu == True, applies leaky ReLU instead
        bn (bool): if True, uses batch normalization
        dropout (float): dropout probability
    """

    def __init__(self, in_channels, out_channels, kernel_size, stride=1,
                 padding=0, bias=True, dilation=1, relu=False, leak=0.,
                 dropout=0., bn=False):
        super(Conv2d, self).__init__()
        self.conv = nn.Conv2d(in_channels=in_channels,
                              out_channels=out_channels,
                              kernel_size=kernel_size,
                              stride=stride,
                              padding=padding,
                              dilation=dilation,
                              bias=bias)
        self.bn = nn.BatchNorm2d(out_channels, eps=1e-5, momentum=0.1, affine=True) if bn else None
        self.dropout = nn.Dropout(p=dropout, inplace=True) if dropout > 0 else None
        if relu:
            if leak > 0:
                self.relu = nn.LeakyReLU(negative_slope=leak, inplace=True)
            elif relu == 'learn':
                self.relu = nn.PReLU()
            elif relu is True:
                self.relu = nn.ReLU(inplace=True)
            else:
                raise ValueError("Unknown argument specified for ReLU activation")
        else:
            self.relu = None

        # Weights initializer
        nn.init.xavier_normal_(self.conv.weight)
        if self.conv.bias is not None:
            nn.init.zeros_(self.conv.bias)

    def forward(self, x):
        x = self.conv(x)
        if self.bn:
            x = self.bn(x)
        if self.dropout:
            x = self.dropout(x)
        if self.relu:
            x = self.relu(x)
        return x

    def output_size(self, input_size):
        """Computes output size
        Args:
            input_size (tuple): (C_in, H_in, W_in)
        """
        _, H_in, W_in = input_size
        C_out = self.conv.out_channels
        kernel_size = self.conv.kernel_size[0]
        padding = self.conv.padding[0]
        stride = self.conv.stride[0]
        H_out = int(np.floor((H_in - kernel_size + 2 * padding) / stride + 1))
        W_out = int(np.floor((W_in - kernel_size + 2 * padding) / stride + 1))
        return (C_out, H_out, W_out)


class ConvTranspose2d(nn.Module):
    """Conv2d + BatchNorm + Dropout + ReLU
    Args:
        in_channels (int): Number of channels in the input image
        out_channels (int): Number of channels produced by the convolution
        kernel_size (int or tuple): Size of the convolving kernel
        stride (int or tuple, optional): stride for the cross-correlation. Default: 1
        padding (int or tuple, optional): zero-padding will be added to both sides of each dimension in the inpu
        output_padding (int or tuple, optional): controls the additional size added to one side of the output shape. Default: 0
        dilation (int or tuple, optional): Spacing between kernel elements. Default: 1
        relu (bool, str): if True, uses ReLU - if 'learn', uses PReLU
        leak (float): if > 0 and relu == True, applies leaky ReLU instead
        dropout (float): dropout probability
        bn (bool): if True, uses batch normalization
    """

    def __init__(self, in_channels, out_channels, kernel_size, stride=1,
                 padding=1, output_padding=0, bias=True, dilation=1, relu=False,
                 leak=0., dropout=0., bn=False):
        super(ConvTranspose2d, self).__init__()
        self.conv = nn.ConvTranspose2d(in_channels=in_channels,
                                       out_channels=out_channels,
                                       kernel_size=kernel_size,
                                       stride=stride,
                                       padding=padding,
                                       output_padding=output_padding,
                                       dilation=dilation,
                                       bias=bias)
        self.bn = nn.BatchNorm2d(out_channels, eps=1e-5, momentum=0.1, affine=True) if bn else None
        self.dropout = nn.Dropout(p=dropout, inplace=True) if dropout > 0 else None
        if relu:
            if leak > 0:
                self.relu = nn.LeakyReLU(negative_slope=leak, inplace=True)
            elif relu == 'learn':
                self.relu = nn.PReLU()
            elif relu is True:
                self.relu = nn.ReLU(inplace=True)
            else:
                raise ValueError("Unknown argument specified for ReLU activation")
        else:
            self.relu = None

        # Weights initializer
        nn.init.xavier_normal_(self.conv.weight)
        if self.conv.bias is not None:
            nn.init.zeros_(self.conv.bias)

    def forward(self, x):
        x = self.conv(x)
        if self.bn:
            x = self.bn(x)
        if self.dropout:
            x = self.dropout(x)
        if self.relu:
            x = self.relu(x)
        return x

    def output_size(self, input_size):
        """Computes output size
        Args:
            input_size (tuple): (C, H_in, W_in)
        """
        raise NotImplementedError


class ResBlock(nn.Module):
    """2D-Convolutional residual unit

    # TODO : add ascii drawing of block

    Args:
        in_channels (int): Number of channels in the input image
        out_channels (int): Number of channels produced by the convolution
        scaling (float): residual scaling factor
        stride (int or tuple, optional): Stride of the convolution. Default: 1
        bias (bool): if True, uses bias parameter in convolutions
    """
    def __init__(self, in_channels, out_channels, kernel_size=3, stride=1,
                 padding=1, scaling=0.1, bias=False, leak=0.):
        super().__init__()
        self.conv1 = Conv2d(in_channels, out_channels, kernel_size=kernel_size, stride=stride,
                            padding=padding, bias=bias, relu=True, leak=leak, bn=True)
        self.conv2 = Conv2d(out_channels, out_channels, kernel_size=3, stride=1,
                            padding=1, bias=bias, relu=False, bn=True)
        self.adjust_identity = None
        if stride > 1 or in_channels != out_channels:
            self.adjust_identity = Conv2d(in_channels, out_channels, kernel_size=1, stride=stride,
                                          bias=False, dilation=1, relu=False, bn=True)
        self.scaling = scaling
        self.relu = nn.ReLU(inplace=True)

    def forward(self, x):
        buffer = self.conv1(x)
        residual = self.conv2(buffer)

        if self.adjust_identity is not None:
            x = self.adjust_identity(x)

        residual = residual.mul(self.scaling)
        x = x.add(residual)
        x = self.relu(x)
        return x
