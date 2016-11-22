import os

import numpy as np
import numpy.random as npr
import tensorflow as tf
import tflearn

import bundle_entropy
from replay_memory import ReplayMemory
from helper import variable_summaries

flags = tf.app.flags
FLAGS = flags.FLAGS

# Input Convex Neural Network

class Agent:

    def __init__(self, dimO, dimA):
        dimA, dimO = dimA[0], dimO[0]
        self.dimA = dimA
        self.dimO = dimO

        tau = FLAGS.tau
        discount = FLAGS.discount
        l2norm = FLAGS.l2norm
        learning_rate = FLAGS.rate
        outheta = FLAGS.outheta
        ousigma = FLAGS.ousigma

        if FLAGS.icnn_opt == 'adam':
            self.opt = self.adam
        elif FLAGS.icnn_opt == 'bundle_entropy':
            self.opt = self.bundle_entropy
        else:
            raise RuntimeError("Unrecognized ICNN optimizer: "+FLAGS.icnn_opt)

        self.rm = ReplayMemory(FLAGS.rmsize, dimO, dimA)
        self.sess = tf.Session(config=tf.ConfigProto(
            inter_op_parallelism_threads=FLAGS.thread,
            log_device_placement=False,
            allow_soft_placement=True,
            gpu_options=tf.GPUOptions(per_process_gpu_memory_fraction=0.1)))

        self.noise = np.ones(dimA)

        obs = tf.placeholder(tf.float32, [None, dimO], "obs")
        act = tf.placeholder(tf.float32, [None, dimA], "act")
        rew = tf.placeholder(tf.float32, [None], "rew")
        negQ = self.negQ(obs, act)
        q = -negQ
        act_grad, = tf.gradients(negQ, act)
        # q_entropy = q + entropy(act)

        obs2 = tf.placeholder(tf.float32, [None, dimO], "obs2")
        act2 = tf.placeholder(tf.float32, [None, dimA], "act2")
        term2 = tf.placeholder(tf.bool, [None], "term2")
        negQ2 = self.negQ(obs2, act2, reuse=True)
        act2_grad, = tf.gradients(negQ2, act2)
        q2 = -negQ2
        # q2_entropy = q2 + entropy(act2)

        if FLAGS.icnn_opt == 'adam':
            q_target = tf.select(term2, rew, rew + discount * q2)
            q_target = tf.maximum(q - 1., q_target)
            q_target = tf.minimum(q + 1., q_target)
            q_target = tf.stop_gradient(q_target)
            td_error = q - q_target
        elif FLAGS.icnn_opt == 'bundle_entropy':
            raise RuntimError("Needs checking.")
            q_target = tf.select(term2, rew, rew + discount * q2_entropy)
            q_target = tf.maximum(q_entropy - 1., q_target)
            q_target = tf.minimum(q_entropy + 1., q_target)
            q_target = tf.stop_gradient(q_target)
            td_error = q_entropy - q_target
        ms_td_error = tf.reduce_mean(tf.square(td_error), 0)

        regLosses = tf.get_collection(tf.GraphKeys.REGULARIZATION_LOSSES)
        loss_q = ms_td_error + l2norm*tf.reduce_sum(regLosses)

        # q optimization
        optim_q = tf.train.AdamOptimizer(learning_rate=learning_rate)
        grads_and_vars_q = optim_q.compute_gradients(loss_q)
        optimize_q = optim_q.apply_gradients(grads_and_vars_q)

        self.theta_ = tf.trainable_variables()
        self.theta_cvx_ = [v for v in self.theta_
                           if 'proj' in v.name and 'W:' in v.name]
        self.makeCvx = [v.assign(tf.abs(v)) for v in self.theta_cvx_]
        self.proj = [v.assign(tf.maximum(v, 0)) for v in self.theta_cvx_]
        # self.proj = [v.assign(tf.abs(v)) for v in self.theta_cvx_]

        summary_writer = tf.train.SummaryWriter(os.path.join(FLAGS.outdir, 'board'),
                                                self.sess.graph)
        summary_list = []
        if FLAGS.icnn_opt == 'adam':
            summary_list.append(tf.scalar_summary('Qvalue', tf.reduce_mean(q)))
        elif FLAGS.icnn_opt == 'bundle_entropy':
            summary_list.append(tf.scalar_summary('Qvalue',
                                                  tf.reduce_mean(q_entropy)))
        summary_list.append(tf.scalar_summary('loss', ms_td_error))
        summary_list.append(tf.scalar_summary('reward', tf.reduce_mean(rew)))

        # tf functions
        with self.sess.as_default():
            self._train = Fun([obs, act, rew, obs2, act2, term2],
                              [optimize_q, loss_q], summary_list, summary_writer)
            self._opt_test = Fun([obs, act], [negQ, act_grad])
            self._opt_train = Fun([obs2, act2], [negQ2, act2_grad])
            # self._opt_test_entr = Fun([obs, act], [loss_test_entr, act_grad_entr])
            # self._opt_train_entr = Fun([obs2, act2],
            #                            [loss_train2_entr, act2_grad_entr])

        # initialize tf variables
        self.saver = tf.train.Saver(max_to_keep=1)
        ckpt = tf.train.latest_checkpoint(FLAGS.outdir + "/tf")
        if ckpt:
            self.saver.restore(self.sess, ckpt)
        else:
            self.sess.run(tf.initialize_all_variables())
            self.sess.run(self.makeCvx)

        self.sess.graph.finalize()

        self.t = 0  # global training time (number of observations)

    def bundle_entropy(self, func, obs):
        act = np.ones((obs.shape[0], self.dimA)) * 0.5
        def fg(x):
            value, grad = func(obs, 2 * x - 1)
            grad *= 2
            return value, grad

        act = bundle_entropy.solveBatch(fg, act)[0]
        act = 2 * act - 1

        return act

    def adam(self, func, obs):
        b1 = 0.9
        b2 = 0.999
        lam = 0.5
        eps = 1e-8
        alpha = 0.01
        nBatch = obs.shape[0]
        act = np.zeros((nBatch, self.dimA))
        m = np.zeros_like(act)
        v = np.zeros_like(act)

        b1t, b2t = 1., 1.
        act_best, a_diff, f_best = [None]*3
        for i in range(1000):
            f, g = func(obs, act)

            if i == 0:
                act_best = act.copy()
                f_best = f.copy()
            else:
                I = (f < f_best)
                act_best[I] = act[I]
                f_best[I] = f[I]

            m = b1 * m + (1. - b1) * g
            v = b2 * v + (1. - b2) * (g * g)
            b1t *= b1
            b2t *= b2
            mhat = m/(1.-b1t)
            vhat = v/(1.-b2t)

            prev_act = act.copy()
            act -= alpha * mhat / (np.sqrt(v) + eps)
            act = np.clip(act, -1, 1)

            a_diff_i = np.mean(np.linalg.norm(act - prev_act, axis=1))
            a_diff = a_diff_i if a_diff is None else lam*a_diff + (1.-lam)*a_diff_i
            # print(a_diff_i, a_diff, np.sum(f))
            if a_diff_i == 0 or a_diff < 1e-3:
                print('  + ADAM took {} iterations'.format(i))
                return act_best

        print('  + Warning: ADAM did not converge.')
        return act_best

    def reset(self, obs):
        self.noise = np.ones(self.dimA)
        self.observation = obs  # initial observation

    def act(self, test=False):
        with self.sess.as_default():
            print('--- Selecting action, test={}'.format(test))
            obs = np.expand_dims(self.observation, axis=0)

            if FLAGS.icnn_opt == 'adam':
                # f = self._opt_test_entr
                f = self._opt_test
            elif FLAGS.icnn_opt == 'bundle_entropy':
                f = self._opt_test
            else:
                raise RuntimeError("Unrecognized ICNN optimizer: "+FLAGS.icnn_opt)

            tflearn.is_training(False)
            action = self.opt(f, obs)
            tflearn.is_training(not test)

            if not test:
                self.noise -= FLAGS.outheta*self.noise - \
                              FLAGS.ousigma*npr.randn(self.dimA)
                action += self.noise

            # action = np.clip(action, -1, 1)
            self.action = np.atleast_1d(np.squeeze(action, axis=0))
            return self.action

    def observe(self, rew, term, obs2, test=False):
        obs1 = self.observation
        self.observation = obs2

        # train
        if not test:
            self.t = self.t + 1

            self.rm.enqueue(obs1, term, self.action, rew)

            if self.t > FLAGS.warmup:
                for i in range(FLAGS.iter):
                    loss = self.train()

    def train(self):
        with self.sess.as_default():
            obs, act, rew, ob2, term2, info = self.rm.minibatch(size=FLAGS.bsize)
            if FLAGS.icnn_opt == 'adam':
                # f = self._opt_train_entr
                f = self._opt_train
            elif FLAGS.icnn_opt == 'bundle_entropy':
                f = self._opt_train
            else:
                raise RuntimeError("Unrecognized ICNN optimizer: "+FLAGS.icnn_opt)
            print('--- Optimizing for training')
            tflearn.is_training(False)
            act2 = self.opt(f, ob2)
            tflearn.is_training(True)

            _, loss = self._train(obs, act, rew, ob2, act2, term2,
                                  log=FLAGS.summary, global_step=self.t)
            self.sess.run(self.proj)
            return loss

    def negQ(self, x, y, reuse=False):
        szs = [FLAGS.l1size, FLAGS.l2size]
        assert(len(szs) >= 1)
        fc = tflearn.fully_connected
        bn = tflearn.batch_normalization

        if reuse:
            tf.get_variable_scope().reuse_variables()

        nLayers = len(szs)
        us = []
        zs = []
        z_zs = []
        z_ys = []
        z_us = []

        reg = 'L2'

        prevU = x
        for i in range(nLayers):
            with tf.variable_scope('u'+str(i)) as s:
                u = fc(prevU, szs[i], reuse=reuse, scope=s, regularizer=reg)
                if i < nLayers-1:
                    u = tf.nn.relu(u)
                    if FLAGS.icnn_bn:
                        u = bn(u, reuse=reuse, scope=s, name='bn')
            us.append(u)
            prevU = u

        prevU, prevZ = x, y
        for i in range(nLayers+1):
            sz = szs[i] if i < nLayers else 1
            z_add = []
            if i > 0:
                with tf.variable_scope('z{}_zu_u'.format(i)) as s:
                    zu_u = fc(prevU, szs[i-1], reuse=reuse, scope=s,
                              activation='relu', bias=True, regularizer=reg)
                with tf.variable_scope('z{}_zu_proj'.format(i)) as s:
                    z_zu = fc(tf.mul(prevZ, zu_u), sz, reuse=reuse, scope=s,
                              bias=False, regularizer=reg)
                z_zs.append(z_zu)
                z_add.append(z_zu)

            with tf.variable_scope('z{}_yu_u'.format(i)) as s:
                yu_u = fc(prevU, self.dimA, reuse=reuse, scope=s, bias=True,
                          regularizer=reg)
            with tf.variable_scope('z{}_yu'.format(i)) as s:
                z_yu = fc(tf.mul(y, yu_u), sz, reuse=reuse, scope=s, bias=False,
                          regularizer=reg)
                z_ys.append(z_yu)
            z_add.append(z_yu)

            with tf.variable_scope('z{}_u'.format(i)) as s:
                z_u = fc(prevU, sz, reuse=reuse, scope=s, bias=True, regularizer=reg)
            z_us.append(z_u)
            z_add.append(z_u)

            z = tf.add_n(z_add)
            variable_summaries(z, 'z{}_preact'.format(i))
            if i < nLayers:
                z = tf.nn.relu(z)
                variable_summaries(z, 'z{}_act'.format(i))

            zs.append(z)
            prevU = us[i] if i < nLayers else None
            prevZ = z

        z = tf.reshape(z, [-1], name='energies')
        return z


    def __del__(self):
        self.sess.close()


# Tensorflow utils
#
class Fun:
    """ Creates a python function that maps between inputs and outputs in the computational graph. """

    def __init__(self, inputs, outputs, summary_ops=None, summary_writer=None, session=None):
        self._inputs = inputs if type(inputs) == list else [inputs]
        self._outputs = outputs
        self._summary_op = tf.merge_summary(summary_ops) if type(summary_ops) == list else summary_ops
        self._session = session or tf.get_default_session()
        self._writer = summary_writer

    def __call__(self, *args, **kwargs):
        """
        Arguments:
          **kwargs: input values
          log: if True write summary_ops to summary_writer
          global_step: global_step for summary_writer
        """
        log = kwargs.get('log', False)

        feeds = {}
        for (argpos, arg) in enumerate(args):
            feeds[self._inputs[argpos]] = arg

        out = self._outputs + [self._summary_op] if log else self._outputs
        res = self._session.run(out, feeds)

        if log:
            i = kwargs['global_step']
            self._writer.add_summary(res[-1], global_step=i)
            res = res[: -1]

        return res


def exponential_moving_averages(theta, tau=0.001):
    ema = tf.train.ExponentialMovingAverage(decay=1 - tau)
    update = ema.apply(theta)  # also creates shadow vars
    averages = [ema.average(x) for x in theta]
    return averages, update


def entropy(x): #the real concave entropy function
    x_move_reg = tf.clip_by_value((x + 1) / 2, 0.0001, 0.9999)
    pen = x_move_reg * tf.log(x_move_reg) + (1 - x_move_reg) * tf.log(1 - x_move_reg)
    return -tf.reduce_sum(pen, 1)
