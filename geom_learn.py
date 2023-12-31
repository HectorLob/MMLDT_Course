# uncompyle6 version 3.9.0
# Python bytecode version base 3.7.0 (3394)
# Decompiled from: Python 3.6.15 | packaged by conda-forge | (default, Dec  3 2021, 18:49:41) 
# [GCC 9.4.0]
# Embedded file name: geom_learn.py
# Compiled at: 2021-09-24 21:49:31
# Size of source mod 2**32: 24975 bytes

import torch
from torch.autograd import Variable, grad
import torch.utils.data as Data
import torch.nn as nn
import torch.nn.functional as F_
from torch.nn.init import xavier_uniform_
import numpy as np, copy


loss_func = nn.MSELoss()

DEBUG = False

class Multiply(nn.Module):

    def __init__(self):
        super(Multiply, self).__init__()

    def forward(self, inp_list):
        result = torch.ones(inp_list[0].size())
        for x in inp_list:
            result *= x

        return result


import torch_geometric as torchG
from torch_geometric.nn import GCNConv, global_mean_pool

class GNN(nn.Module):

    def __init__(self, inp, out, hidden):
        super(GNN, self).__init__()
        self.conv1 = GCNConv(inp, hidden)
        self.bn1 = nn.BatchNorm1d(hidden)
        self.drop1 = nn.Dropout(p=0.5)
        self.lin1 = nn.Linear(hidden, (hidden // 2), bias=True)
        self.bn2 = nn.BatchNorm1d(hidden // 2)
        self.drop2 = nn.Dropout(p=0.5)
        xavier_uniform_(self.lin1.weight)
        self.lin2 = nn.Linear((hidden // 2), out, bias=True)
        self.bn3 = nn.BatchNorm1d(out)
        xavier_uniform_(self.lin2.weight)

    def forward(self, x, edge_index, batch):
        x = self.conv1(x, edge_index)
        x = self.bn1(x)
        x = F_.relu(x)
        x = self.drop1(x)
        x = global_mean_pool(x, batch)
        x = self.lin1(x)
        x = self.bn2(x)
        x = F_.relu(x)
        x = self.drop2(x)
        x = self.lin2(x)
        x = self.bn3(x)
        return x


class MixGNN(nn.Module):

    def __init__(self, inp, out, hidden, gnn_inp, gnn_out, gnn_hidden):
        super(MixGNN, self).__init__()
        self.gnn = GNN(gnn_inp, gnn_out, gnn_hidden)
        self.fc1 = nn.Linear((inp + gnn_out), hidden, bias=True)
        self.bn4 = nn.BatchNorm1d(hidden)
        self.drop4 = nn.Dropout(p=0.5)
        xavier_uniform_(self.fc1.weight)
        self.mult1 = Multiply()
        self.fc2 = nn.Linear(hidden, (hidden // 4), bias=True)
        self.bn5 = nn.BatchNorm1d(hidden // 4)
        self.drop5 = nn.Dropout(p=0.3)
        xavier_uniform_(self.fc2.weight)
        self.mult2 = Multiply()
        self.mult3 = Multiply()
        self.fc3 = nn.Linear((hidden // 4), out, bias=True)

    def forward(self, x, masks, gnn_x, edge_index, gnn_batch, debug=False):
        gnn_x = self.gnn(gnn_x, edge_index, gnn_batch)
        gnn_x = gnn_x[masks, :]
        h = torch.cat((x, gnn_x), axis=1)
        h = self.fc1(h)
        h = self.bn4(h)
        h = F_.relu(h)
        h = self.mult1([h, h])
        h = self.drop4(h)
        h = self.fc2(h)
        h = self.bn5(h)
        h = F_.relu(h)
        h = self.mult2([h, h])
        h = self.mult3([h, h])
        h = self.drop5(h)
        h = self.fc3(h)
        return h

    def predict_energy(self, x, masks, gnn_x, edge_index, gnn_batch, data_scales):
        in_scales, out_scale, grad_scales, grad_limits = data_scales
        y = self(x, masks, gnn_x, edge_index, gnn_batch)
        grad_y, = grad((y.sum()), x, create_graph=True)
        grad_y /= torch.tensor(in_scales) * out_scale
        grad_y -= torch.tensor(grad_limits)
        grad_y /= torch.tensor(grad_scales)
        return (x.detach().numpy(), y.detach().numpy(), grad_y.detach().numpy())


class EnergyNet(nn.Module):

    def __init__(self, inp, out, hidden=100):
        super(EnergyNet, self).__init__()
        self.fc1 = nn.Linear(inp, hidden, bias=True)
        xavier_uniform_(self.fc1.weight)
        self.mult1 = Multiply()
        self.fc2 = nn.Linear(hidden, (hidden // 4), bias=True)
        xavier_uniform_(self.fc2.weight)
        self.mult2 = Multiply()
        self.mult3 = Multiply()
        self.fc4 = nn.Linear((hidden // 4), out, bias=True)

    def forward(self, x):
        x = self.fc1(x)
        x = F_.relu(x)
        x = self.mult1([x, x])
        x = self.fc2(x)
        x = F_.relu(x)
        x = self.mult2([x, x])
        x = self.mult3([x, x])
        x = self.fc4(x)
        return x

    def predict(self, x):
        self.eval()
        y = self(x)
        x = x.cpu().numpy().flatten()
        y = y.cpu().detach().numpy().flatten()
        return [x, y]


def train_L2_simp(net, all_trainX, train_loader, EPOCH, BATCH_SIZE, optimizer, scheduler):
    state = copy.deepcopy(net.state_dict())
    best_loss = np.inf
    num_train_data = len(train_loader.dataset)
    print('# train data: {}'.format(num_train_data))
    terminate_training = False
    eps = 1e-05
    for epoch in range(EPOCH):
        batch_cnt = 1
        for step, (batch_x, batch_y) in enumerate(train_loader):
            masks = batch_x
            b_y = Variable(batch_y)
            net.eval()
            all_trainY = net(all_trainX)
            output = all_trainY[masks, :]
            net.train()
            loss = loss_func(output, b_y)
            epoch_loss = loss.item()
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            if batch_cnt % 5 == 0:
                print('epoch', epoch, 'lr: {:.7f}'.format(optimizer.param_groups[0]['lr']), 'train total loss: {:.5e}'.format(epoch_loss))
            if epoch_loss < best_loss:
                best_loss = epoch_loss
                state = copy.deepcopy(net.state_dict())
            batch_cnt += 1

        scheduler.step()

    print('Best score:', best_loss)
    return state


def train_H1_scaled(net, train_loader, valid_loader, EPOCH, BATCH_SIZE, optimizer, scheduler, data_scales, lam1):
    state = copy.deepcopy(net.state_dict())
    best_loss = np.inf
    num_train_data = len(train_loader.dataset)
    num_valid_data = len(valid_loader.dataset)
    in_scales, out_scale, grad_scales, grad_limits = data_scales
    lossTotal = np.zeros((EPOCH, 1))
    lossVal = np.zeros((EPOCH, 1))
    lossGrad = np.zeros((EPOCH, 1))
    vlossTotal = np.zeros((EPOCH, 1))
    vlossVal = np.zeros((EPOCH, 1))
    vlossGrad = np.zeros((EPOCH, 1))
    terminate_training = False
    eps = 1e-06
    for epoch in range(EPOCH):
        epoch_mse0 = 0.0
        epoch_mse1 = 0.0
        epoch_val_mse0 = 0.0
        epoch_val_mse1 = 0.0
        for _, (batch_x, batch_y) in enumerate(train_loader):
            b_x = Variable(batch_x, requires_grad=True)
            b_y = Variable(batch_y)
            net.eval()
            output0 = net(b_x)
            output1, = grad((output0.sum()), b_x, create_graph=True)
            b_x.requires_grad = False
            output1 /= torch.tensor(in_scales) * out_scale
            output1 -= torch.tensor(grad_limits)
            output1 /= torch.tensor(grad_scales)
            net.train()
            mse0 = loss_func(output0, b_y[:, 0:1])
            mse1 = loss_func(output1, b_y[:, 1:])
            epoch_mse0 += mse0.detach().item() * BATCH_SIZE
            epoch_mse1 += mse1.detach().item() * BATCH_SIZE
            loss = mse0 + lam1 * mse1
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        for _, (batch_x, batch_y) in enumerate(valid_loader):
            b_x = Variable(batch_x, requires_grad=True)
            b_y = Variable(batch_y)
            net.eval()
            output0 = net(b_x)
            output1, = grad((output0.sum()), b_x, create_graph=True)
            b_x.requires_grad = False
            output1 /= torch.tensor(in_scales) * out_scale
            output1 -= torch.tensor(grad_limits)
            output1 /= torch.tensor(grad_scales)
            epoch_val_mse0 += loss_func(output0, b_y[:, 0:1]).detach().item() * BATCH_SIZE
            epoch_val_mse1 += loss_func(output1, b_y[:, 1:]).detach().item() * BATCH_SIZE

        epoch_mse0 /= num_train_data
        epoch_mse1 /= num_train_data
        epoch_loss = epoch_mse0 + lam1 * epoch_mse1
        epoch_val_mse0 /= num_valid_data
        epoch_val_mse1 /= num_valid_data
        epoch_val_loss = epoch_val_mse0 + lam1 * epoch_val_mse1
        scheduler.step()
        lossTotal[epoch] = epoch_loss
        lossVal[epoch] = epoch_mse0
        lossGrad[epoch] = epoch_mse1
        vlossTotal[epoch] = epoch_val_loss
        vlossVal[epoch] = epoch_val_mse0
        vlossGrad[epoch] = epoch_val_mse1
        if (epoch + 1) % 10 == 0:
            print('    epoch {:4d}'.format(epoch + 1), 'lr: {:.7f}'.format(optimizer.param_groups[0]['lr']), 'train val loss: {:.3e}'.format(epoch_mse0), 'train grad loss: {:.3e}'.format(epoch_mse1), 'train total loss: {:.3e}'.format(epoch_loss), 'valid val loss: {:.3e}'.format(epoch_val_mse0), 'valid grad loss: {:.3e}'.format(epoch_val_mse1), 'valid total loss: {:.3e}'.format(epoch_val_loss))
        if epoch_loss < best_loss:
            best_loss = epoch_loss
            state = copy.deepcopy(net.state_dict())

    print('Best score:', best_loss)
    return (state, lossTotal, lossVal, lossGrad, vlossTotal, vlossVal, vlossGrad)


def train_H1_scaled_hybrid(net, train_loaders, valid_loaders, graph_loaders, EPOCH, BATCH_SIZE, optimizer, scheduler, data_scales, lam1):
    best_loss = np.inf
    num_train_data = np.array([len(train_loader.dataset) for train_loader in train_loaders], dtype=(np.int32)).sum()
    num_valid_data = np.array([len(val_loader.dataset) for val_loader in valid_loaders], dtype=(np.int32)).sum()
    in_scales, out_scale, grad_scales, grad_limits = data_scales
    lossTotal = np.zeros((EPOCH, 1))
    lossVal = np.zeros((EPOCH, 1))
    lossGrad = np.zeros((EPOCH, 1))
    vlossTotal = np.zeros((EPOCH, 1))
    vlossVal = np.zeros((EPOCH, 1))
    vlossGrad = np.zeros((EPOCH, 1))
    terminate_training = False
    eps = 1e-06
    for epoch in range(EPOCH):
        epoch_mse0 = 0.0
        epoch_mse1 = 0.0
        epoch_val_mse0 = 0.0
        epoch_val_mse1 = 0.0
        for bch in range(len(train_loaders)):
            train_loader = train_loaders[bch]
            valid_loader = valid_loaders[bch]
            graph_loader = graph_loaders[bch]
            num_gnn_batch = 0
            for _, temp_data in enumerate(graph_loader):
                num_gnn_batch += 1

            assert num_gnn_batch == 1
            gnn_data = copy.deepcopy(temp_data)
            for cnt, (batch_x, batch_y) in enumerate(train_loader):
                b_x = Variable((batch_x[:, :-1]), requires_grad=True)
                b_y = Variable(batch_y)
                masks = batch_x[:, -1].long()
                net.eval()
                output0 = net(b_x, masks, (gnn_data.x), (gnn_data.edge_index), (gnn_data.batch), debug=DEBUG)
                output1, = grad((output0.sum()), b_x, create_graph=True)
                b_x.requires_grad = False
                output1 /= torch.tensor(in_scales) * out_scale
                output1 -= torch.tensor(grad_limits)
                output1 /= torch.tensor(grad_scales)
                net.train()
                mse0 = loss_func(output0, b_y[:, 0:1])
                mse1 = loss_func(output1, b_y[:, 1:])
                epoch_mse0 += mse0.detach().item() * BATCH_SIZE
                epoch_mse1 += mse1.detach().item() * BATCH_SIZE
                loss = mse0 + lam1 * mse1
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()

            for _, (batch_x, batch_y) in enumerate(valid_loader):
                b_x = Variable((batch_x[:, :-1]), requires_grad=True)
                b_y = Variable(batch_y)
                masks = batch_x[:, -1].long()
                num_gnn_batch = 0
                for _, temp_data in enumerate(graph_loader):
                    num_gnn_batch += 1

                assert num_gnn_batch == 1
                gnn_data = copy.deepcopy(temp_data)
                net.eval()
                output0 = net(b_x, masks, gnn_data.x, gnn_data.edge_index, gnn_data.batch)
                output1, = grad((output0.sum()), b_x, create_graph=True)
                b_x.requires_grad = False
                output1 /= torch.tensor(in_scales) * out_scale
                output1 -= torch.tensor(grad_limits)
                output1 /= torch.tensor(grad_scales)
                epoch_val_mse0 += loss_func(output0, b_y[:, 0:1]).detach().item() * BATCH_SIZE
                epoch_val_mse1 += loss_func(output1, b_y[:, 1:]).detach().item() * BATCH_SIZE

        epoch_mse0 /= num_train_data
        epoch_mse1 /= num_train_data
        epoch_loss = epoch_mse0 + lam1 * epoch_mse1
        epoch_val_mse0 /= num_valid_data
        epoch_val_mse1 /= num_valid_data
        epoch_val_loss = epoch_val_mse0 + lam1 * epoch_val_mse1
        scheduler.step()
        lossTotal[epoch] = epoch_loss
        lossVal[epoch] = epoch_mse0
        lossGrad[epoch] = epoch_mse1
        vlossTotal[epoch] = epoch_val_loss
        vlossVal[epoch] = epoch_val_mse0
        vlossGrad[epoch] = epoch_val_mse1
        if not (epoch + 1) % 5 == 0:
            if epoch == 0:
                print('    epoch {:4d}'.format(epoch + 1), 'lr: {:.7f}'.format(optimizer.param_groups[0]['lr']), 'train val loss: {:.3e}'.format(epoch_mse0), 'train grad loss: {:.3e}'.format(epoch_mse1), 'train total loss: {:.3e}'.format(epoch_loss), 'valid val loss: {:.3e}'.format(epoch_val_mse0), 'valid grad loss: {:.3e}'.format(epoch_val_mse1), 'valid total loss: {:.3e}'.format(epoch_val_loss))
            if epoch_loss < best_loss:
                best_loss = epoch_loss
                state = copy.deepcopy(net.state_dict())

    print('Best score:', best_loss)
    return (state, lossTotal, lossVal, lossGrad, vlossTotal, vlossVal, vlossGrad)


def train_H2_scaled_hybrid(net, train_loader, valid_loader, graph_loader, EPOCH, BATCH_SIZE, optimizer, scheduler, data_scales, lam1, lam2):
    state = copy.deepcopy(net.state_dict())
    best_loss = np.inf
    num_train_data = len(train_loader.dataset)
    num_valid_data = len(valid_loader.dataset)
    in_scales, out_scale, grad_scales, grad_limits, hess_scales, hess_limits = data_scales
    lossTotal = np.zeros((EPOCH, 1))
    lossVal = np.zeros((EPOCH, 1))
    lossGrad = np.zeros((EPOCH, 1))
    lossHess = np.zeros((EPOCH, 1))
    vlossTotal = np.zeros((EPOCH, 1))
    vlossVal = np.zeros((EPOCH, 1))
    vlossGrad = np.zeros((EPOCH, 1))
    vlossHess = np.zeros((EPOCH, 1))
    terminate_training = False
    eps = 1e-06
    for epoch in range(EPOCH):
        epoch_mse0 = 0.0
        epoch_mse1 = 0.0
        epoch_mse2 = 0.0
        epoch_val_mse0 = 0.0
        epoch_val_mse1 = 0.0
        epoch_val_mse2 = 0.0
        for _, (batch_x, batch_y) in enumerate(train_loader):
            b_x = Variable((batch_x[:, :-1]), requires_grad=True)
            b_y = Variable(batch_y)
            masks = batch_x[:, -1]
            num_gnn_batch = 0
            for _, temp_data in enumerate(graph_loader):
                num_gnn_batch += 1

            assert num_gnn_batch == 1
            gnn_data = copy.deepcopy(temp_data)
            pert0 = torch.zeros_like(b_x, requires_grad=True)
            pert1 = torch.zeros_like(b_x, requires_grad=True)
            pert0[:, 0] = eps
            pert1[:, 1] = eps
            b_x_pert0 = b_x + pert0
            b_x_pert1 = b_x + pert1
            net.eval()
            output0 = net(b_x, masks, gnn_data.x, gnn_data.edge_index, gnn_data.batch)
            output0.sum().backward(retain_graph=True, create_graph=True)
            output1 = b_x.grad
            output0_pert0 = net(b_x_pert0, masks, gnn_data.x, gnn_data.edge_index, gnn_data.batch)
            output0_pert1 = net(b_x_pert1, masks, gnn_data.x, gnn_data.edge_index, gnn_data.batch)
            output0_pert0.sum().backward(retain_graph=True, create_graph=True)
            output0_pert1.sum().backward(retain_graph=True, create_graph=True)
            output1_pert0 = b_x_pert0.grad
            output1_pert1 = b_x_pert1.grad
            hess_list0 = 1.0 / eps * (output1_pert0 - output1)
            hess_list1 = 1.0 / eps * (output1_pert1 - output1)
            b_x.requires_grad = False
            b_x_pert0.requires_grad = False
            b_x_pert1.requires_grad = False
            output1 *= torch.tensor(in_scales) * torch.tensor(out_scale)
            output1 *= torch.tensor(grad_scales)
            output1 += torch.tensor(grad_limits)
            hess_list0 *= torch.tensor(in_scales * in_scales[0]) * torch.tensor(out_scale)
            hess_list1 *= torch.tensor(in_scales * in_scales[1]) * torch.tensor(out_scale)
            output2 = torch.cat((hess_list0, hess_list1), 1)
            output2 *= torch.tensor(hess_scales)
            output2 += torch.tensor(hess_limits)
            net.train()
            mse0 = loss_func(output0, b_y[:, 0:1])
            mse1 = loss_func(output1, b_y[:, 1:3])
            mse2 = loss_func(output2, b_y[:, 3:])
            epoch_mse0 += mse0.item() * BATCH_SIZE
            epoch_mse1 += mse1.item() * BATCH_SIZE
            epoch_mse2 += mse2.item() * BATCH_SIZE
            loss = mse0 + lam1 * mse1 + lam2 * mse2
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

        for _, (batch_x, batch_y) in enumerate(valid_loader):
            b_x = Variable((batch_x[:, :-1]), requires_grad=True)
            b_y = Variable(batch_y)
            masks = batch_x[:, -1]
            num_gnn_batch = 0
            for _, temp_data in enumerate(graph_loader):
                num_gnn_batch += 1

            assert num_gnn_batch == 1
            gnn_data = copy.deepcopy(temp_data)
            pert0 = torch.zeros_like(b_x, requires_grad=True)
            pert1 = torch.zeros_like(b_x, requires_grad=True)
            pert0[:, 0] = eps
            pert1[:, 1] = eps
            b_x_pert0 = b_x + pert0
            b_x_pert1 = b_x + pert1
            net.eval()
            output0 = net(b_x, masks, gnn_data.x, gnn_data.edge_index, gnn_data.batch)
            output0.sum().backward(retain_graph=True, create_graph=True)
            output1 = b_x.grad
            output0_pert0 = net(b_x_pert0, masks, gnn_data.x, gnn_data.edge_index, gnn_data.batch)
            output0_pert1 = net(b_x_pert1, masks, gnn_data.x, gnn_data.edge_index, gnn_data.batch)
            output0_pert0.sum().backward(retain_graph=True, create_graph=True)
            output0_pert1.sum().backward(retain_graph=True, create_graph=True)
            output1_pert0 = b_x_pert0.grad
            output1_pert1 = b_x_pert1.grad
            hess_list0 = 1.0 / eps * (output1_pert0 - output1)
            hess_list1 = 1.0 / eps * (output1_pert1 - output1)
            b_x.requires_grad = False
            b_x_pert0.requires_grad = False
            b_x_pert1.requires_grad = False
            output1 *= torch.tensor(in_scales) * torch.tensor(out_scale)
            output1 *= torch.tensor(grad_scales)
            output1 += torch.tensor(grad_limits)
            hess_list0 *= torch.tensor(in_scales * in_scales[0]) * torch.tensor(out_scale)
            hess_list1 *= torch.tensor(in_scales * in_scales[1]) * torch.tensor(out_scale)
            output2 = torch.cat((hess_list0, hess_list1), 1)
            output2 *= torch.tensor(hess_scales)
            output2 += torch.tensor(hess_limits)
            epoch_val_mse0 += loss_func(output0, b_y[:, 0:1]).item() * BATCH_SIZE
            epoch_val_mse1 += loss_func(output1, b_y[:, 1:3]).item() * BATCH_SIZE
            epoch_val_mse2 += loss_func(output2, b_y[:, 3:]).item() * BATCH_SIZE

        epoch_mse0 /= num_train_data
        epoch_mse1 /= num_train_data
        epoch_mse2 /= num_train_data
        epoch_loss = epoch_mse0 + lam1 * epoch_mse1 + lam2 * epoch_mse2
        epoch_val_mse0 /= num_valid_data
        epoch_val_mse1 /= num_valid_data
        epoch_val_mse2 /= num_valid_data
        epoch_val_loss = epoch_val_mse0 + lam1 * epoch_val_mse1 + lam2 * epoch_val_mse2
        scheduler.step()
        lossTotal[epoch] = epoch_loss
        lossVal[epoch] = epoch_mse0
        lossGrad[epoch] = epoch_mse1
        lossHess[epoch] = epoch_mse2
        vlossTotal[epoch] = epoch_val_loss
        vlossVal[epoch] = epoch_val_mse0
        vlossGrad[epoch] = epoch_val_mse1
        vlossHess[epoch] = epoch_val_mse2
        if epoch % 10 == 0:
            print('epoch', epoch, 'lr: {:.7f}'.format(optimizer.param_groups[0]['lr']), 'train val loss: {:.3e}'.format(epoch_mse0), 'train grad loss: {:.3e}'.format(epoch_mse1), 'train hess loss: {:.3e}'.format(epoch_mse2), 'train total loss: {:.3e}'.format(epoch_loss), 'valid val loss: {:.3e}'.format(epoch_val_mse0), 'valid grad loss: {:.3e}'.format(epoch_val_mse1), 'valid hess loss: {:.3e}'.format(epoch_val_mse2), 'valid total loss: {:.3e}'.format(epoch_val_loss))
        if epoch_loss < best_loss:
            best_loss = epoch_loss
            state = copy.deepcopy(net.state_dict())

    print('Best score:', best_loss)
    return (state, lossTotal, lossVal, lossGrad, lossHess, vlossTotal, vlossVal, vlossGrad, vlossHess)
# okay decompiling geom_learn.pyc
