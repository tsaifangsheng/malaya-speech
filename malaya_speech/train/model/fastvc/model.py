import tensorflow as tf
from ..fastspeech.model import (
    TFFastSpeechEncoder,
    TFTacotronPostnet,
    TFEmbedding,
)
import numpy as np


class ConvNorm(tf.keras.layers.Layer):
    def __init__(
        self,
        out_channels,
        kernel_size = 1,
        stride = 1,
        padding = 'SAME',
        dilation = 1,
        bias = True,
        **kwargs,
    ):
        super(ConvNorm, self).__init__(name = 'ConvNorm', **kwargs)
        self.conv = tf.keras.layers.Conv1D(
            out_channels,
            kernel_size = kernel_size,
            strides = stride,
            padding = padding,
            dilation_rate = dilation,
            use_bias = bias,
        )

    def call(self, x):
        return self.conv(x)


class Encoder(tf.keras.layers.Layer):
    def __init__(self, dim_neck, freq, **kwargs):
        super(Encoder, self).__init__(name = 'Encoder', **kwargs)
        self.dim_neck = dim_neck
        self.freq = freq

        self.convolutions = []
        for i in range(3):
            convolutions = tf.keras.Sequential()
            convolutions.add(
                ConvNorm(512, kernel_size = 5, stride = 1, dilation = 1)
            )
            convolutions.add(tf.keras.layers.BatchNormalization())
            self.convolutions.append(convolutions)

        self.lstm = tf.keras.Sequential()
        for i in range(2):
            self.lstm.add(
                tf.keras.layers.Bidirectional(
                    tf.keras.layers.LSTM(dim_neck, return_sequences = True)
                )
            )

    def call(self, x, c_org, training = True):
        c_org = tf.tile(tf.expand_dims(c_org, 1), (1, tf.shape(x)[1], 1))
        x = tf.concat([x, c_org], axis = -1)
        for c in self.convolutions:
            x = c(x, training = training)
            x = tf.nn.relu(x)
        outputs = self.lstm(x)
        out_forward = outputs[:, :, : self.dim_neck]
        out_backward = outputs[:, :, self.dim_neck :]

        lens = tf.shape(outputs)[1]

        codes = tf.TensorArray(
            dtype = tf.float32,
            size = 0,
            dynamic_size = True,
            infer_shape = True,
        )
        init_state = (0, 0, codes)

        def condition(i, counter, codes):
            return i < lens

        def body(i, counter, codes):
            c = tf.concat(
                [out_forward[:, i + self.freq - 1, :], out_backward[:, i, :]],
                axis = -1,
            )
            return i + self.freq, counter + 1, codes.write(counter, c)

        _, _, codes = tf.while_loop(condition, body, init_state)
        codes = codes.stack()

        return codes


class Decoder(tf.keras.layers.Layer):
    def __init__(self, config, **kwargs):
        super(Decoder, self).__init__(name = 'Decoder', **kwargs)
        self.config = config
        self.encoder = TFFastSpeechEncoder(config, name = 'encoder')
        self.position_embeddings = TFEmbedding(
            config.max_position_embeddings + 1,
            config.hidden_size,
            weights = [self._sincos_embedding()],
            name = 'position_embeddings',
            trainable = False,
        )

    def call(self, x, attention_mask, training = True):
        input_shape = tf.shape(x)
        seq_length = input_shape[1]

        position_ids = tf.range(1, seq_length + 1, dtype = tf.int32)[
            tf.newaxis, :
        ]
        position_embeddings = self.position_embeddings(position_ids)
        x = x + tf.cast(position_embeddings, x.dtype)
        return self.encoder([x, attention_mask])[0]

    def _sincos_embedding(self):
        position_enc = np.array(
            [
                [
                    pos
                    / np.power(10000, 2.0 * (i // 2) / self.config.hidden_size)
                    for i in range(self.config.hidden_size)
                ]
                for pos in range(self.config.max_position_embeddings + 1)
            ]
        )

        position_enc[:, 0::2] = np.sin(position_enc[:, 0::2])
        position_enc[:, 1::2] = np.cos(position_enc[:, 1::2])

        # pad embedding.
        position_enc[0] = 0.0

        return position_enc


class Model(tf.keras.Model):
    def __init__(self, config, dim_neck, freq, **kwargs):
        super(Model, self).__init__(name = 'fastvc', **kwargs)
        self.encoder = Encoder(dim_neck, freq)
        self.decoder = Decoder(config.decoder_self_attention_params)
        self.mel_dense = tf.keras.layers.Dense(
            units = config.num_mels, dtype = tf.float32, name = 'mel_before'
        )
        self.postnet = TFTacotronPostnet(
            config = config, dtype = tf.float32, name = 'postnet'
        )
        self.config = config

    def call_second(self, x, c_org, training = True):
        return self.encoder(x, c_org, training = training)

    def call(self, x, c_org, c_trg, mel_lengths, training = True, **kwargs):

        max_length = tf.cast(tf.reduce_max(mel_lengths), tf.int32)
        attention_mask = tf.sequence_mask(
            lengths = mel_lengths, maxlen = max_length, dtype = tf.float32
        )
        attention_mask.set_shape((None, None))
        codes = self.encoder(x, c_org, training = training)  # [STACK, B, D]

        tmp = tf.TensorArray(
            dtype = tf.float32,
            size = 0,
            dynamic_size = True,
            infer_shape = True,
        )
        init_state = (0, tmp)
        stack_size = tf.shape(codes)[0]

        def condition(i, tmp):
            return i < stack_size

        def body(i, tmp):
            c = tf.expand_dims(codes[i], 1)
            c = tf.tile(
                c, (1, tf.cast(tf.shape(x)[1] / stack_size, tf.int32), 1)
            )
            return i + 1, tmp.write(i, c)

        _, tmp = tf.while_loop(condition, body, init_state)
        tmp = tmp.stack()  # [STACK, B, T, D]
        code_exp = tf.reshape(
            tmp, (tf.shape(x)[0], -1, self.encoder.dim_neck * 2)
        )
        c_trg = tf.tile(tf.expand_dims(c_trg, 1), (1, tf.shape(x)[1], 1))
        encoder_outputs = tf.concat([code_exp, c_trg], axis = -1)
        decoder_output = self.decoder(
            encoder_outputs, attention_mask, training = training
        )
        mel_before = self.mel_dense(decoder_output)
        mel_after = (
            self.postnet([mel_before, attention_mask], training = training)
            + mel_before
        )

        return encoder_outputs, mel_before, mel_after, codes
