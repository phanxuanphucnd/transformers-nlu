# -*- coding: utf-8 -*-
# Copyright (c) 2021 by Phuc Phan

import os
import torch
import logging
import numpy as np
import pandas as pd

from typing import Union
from tqdm import tqdm, trange
from scipy.special import softmax
from transformers import AdamW, get_linear_schedule_with_warmup
from torch.utils.data import DataLoader, RandomSampler, SequentialSampler, TensorDataset

from arizona.utils import set_seed
from arizona.utils import compute_metrics
from arizona.utils import get_from_registry
from arizona.early_stopping import EarlyStopping
from arizona.nlu.models.joint import JointCoBERTa
from arizona.nlu.datasets.data_utils import normalize
from arizona.nlu.datasets.joint_dataset import JointNLUDataset
from arizona.utils import CONFIGS_REGISTRY, MODELS_REGISTRY, MODEL_PATH_MAP

logger = logging.getLogger(__name__)

class JointCoBERTaLearner():
    def __init__(
        self, 
        model: JointCoBERTa=None, 
        model_name_or_path: str=None,
        model_type: str=None,
        device: str=None,
        seed: int=123, 
        **kwargs
    ):
        super(JointCoBERTaLearner, self).__init__()
        
        set_seed(seed)
        
        self.dropout = kwargs.pop('dropout', 0.1)
        self.use_crf = kwargs.pop('use_crf', True)
        self.ignore_index = kwargs.pop('ignore_index', 0)
        self.intent_loss_coef = kwargs.pop('intent_loss_coef', 1.0)
        self.tag_loss_coef = kwargs.pop('tag_loss_coef', 1.0)
        self.use_intent_context_concat = kwargs.pop('use_intent_context_concat', False)
        self.use_intent_context_attention = kwargs.pop('use_intent_context_attention', True)
        self.attention_embedding_dim = kwargs.pop('attention_embedding_dim', 200)
        self.max_seq_len = kwargs.pop('max_seq_len', 50)
        self.intent_embedding_type = kwargs.pop('intent_embedding_type', 'soft')
        self.use_attention_mask = kwargs.pop('use_attention_mask', False)
        self.pad_token_label_id = self.ignore_index

        self.model = model
        self.kwargs =kwargs
        self.model_type = model_type
        self.model_name_or_path = model_name_or_path

        if model_name_or_path:
            self.model_name_or_path = model_name_or_path
        else:
            self.model_name_or_path = model_type

        self.config_class = get_from_registry(self.model_type, CONFIGS_REGISTRY)
        self.config = self.config_class.from_pretrained(
            MODEL_PATH_MAP.get(self.model_name_or_path, model_name_or_path), 
            finetuning_task='NLU'
        )
        self.model_class = get_from_registry(self.model_type, MODELS_REGISTRY)

        if not device:
            self.device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
        else:
            self.device = device

    @property
    def __name__(self):
        return 'JointCoBERTaNLU'

    def train(
        self, 
        train_dataset: JointNLUDataset=None,
        test_dataset: JointNLUDataset=None,
        train_batch_size: int=32,
        eval_batch_size: int=64,
        learning_rate: float=5e-5,
        n_epochs: int=10,
        weight_decay: float=0.0,
        gradient_accumulation_steps: int=1,
        adam_epsilon: float=1e-8,
        max_grad_norm: float=1.0,
        max_steps: int=-1,
        warmup_steps: int=0,
        early_stopping: Union[int, bool]=False,
        tuning_metric: str='loss',
        view_model: bool=True, 
        monitor_test: bool=True,
        save_best_model: bool=True,
        model_dir: str='./model',
        model_name: str='coberta-mini.nlu',
        gpu_id: int=0,
        **kwargs
    ):
        logger.info(f"➖➖➖➖➖ Dataset Info ➖➖➖➖➖")
        logger.info(f"Length of Training dataset: {len(train_dataset)}")
        logger.info(f"Length of Test dataset: {len(test_dataset)}")
        logger.info(f"Description intent classes: {len(train_dataset.intent_labels)} - "
                    f"{train_dataset.processor.intent_labels}")
        logger.info(f"Description tag classes: {len(train_dataset.tag_labels)} - "
                    f"{train_dataset.processor.tag_labels}")

        self.intent_labels = train_dataset.intent_labels
        self.tag_labels = train_dataset.tag_labels
        self.max_seq_len = train_dataset.max_seq_len
        self.tokenizer_name = train_dataset.tokenizer_name

        train_dataset = train_dataset.build_dataset()
        test_dataset = test_dataset.build_dataset()

        if not self.model and not self.model_name_or_path:
            raise ValueError(f"Either parameter `model` or `model_name_or_path` must be not None value !")
        elif not self.model:
            model_ = MODEL_PATH_MAP.get(self.model_name_or_path, self.model_name_or_path)
            self.model = self.model_class.from_pretrained(
                model_,
                config=self.config,
                dropout=self.dropout,
                use_crf=self.use_crf,
                ignore_index=self.ignore_index,
                use_intent_context_concat=self.use_intent_context_concat,
                use_intent_context_attention=self.use_intent_context_attention,
                attention_embedding_dim=self.attention_embedding_dim, 
                max_seq_len=self.max_seq_len,
                intent_embedding_type=self.intent_embedding_type,
                use_attention_mask=self.use_attention_mask,
                intent_loss_coef=self.intent_loss_coef,
                tag_loss_coef=self.tag_loss_coef,
                intent_labels=self.intent_labels,
                tag_labels=self.tag_labels
            )

        torch.cuda.set_device(gpu_id)
        logger.info(f"Current device: {torch.cuda.current_device()}")
        self.model.to(self.device)

        if view_model:
            logger.info(f"➖➖➖➖➖ Model Info ➖➖➖➖➖")
            print(self.model)

        train_sampler = RandomSampler(train_dataset)
        train_dataloader = DataLoader(train_dataset, sampler=train_sampler, batch_size=train_batch_size)

        if max_steps > 0:
            t_total = max_steps
            n_epochs = max_steps // (len(train_dataloader) // gradient_accumulation_steps) + 1
        else:
            t_total = len(train_dataloader) // gradient_accumulation_steps * n_epochs

        # TODO: Prepare optimizer and schedule (Linear warmup and decay)
        no_decay = ['bias', 'LayerNorm.weight']
        optimizer_grouped_parameters = [
            {'params': [p for n, p in self.model.named_parameters() if not any(nd in n for nd in no_decay)],
             'weight_decay': weight_decay},
            {'params': [p for n, p in self.model.named_parameters() if any(nd in n for nd in no_decay)],
             'weight_decay': 0.0}
        ]
 
        optimizer = AdamW(optimizer_grouped_parameters, lr=learning_rate, eps=adam_epsilon)
        scheduler = get_linear_schedule_with_warmup(optimizer, num_warmup_steps=warmup_steps, num_training_steps=t_total)

        logger.info(f"➖➖➖➖➖ Running training ➖➖➖➖➖")
        logger.info(f"Num examples = {len(train_dataset)}")
        logger.info(f"Num epochs = {n_epochs}")
        logger.info(f"Total train batch size = {train_batch_size}")
        logger.info(f"Gradient accumulation steps = {gradient_accumulation_steps}")
        logger.info(f"Total optimization steps = {t_total}")
        logger.info(f"Monitor tests = {monitor_test}")
        logger.info(f"Save best model = {save_best_model}")

        global_step = 0
        tr_loss = 0.0
        self.model.zero_grad()

        train_iterator = trange(int(n_epochs), desc="Epoch")
        if early_stopping:
            early_stopping = EarlyStopping(patience=early_stopping)

        if tuning_metric == 'loss':
            best_score = -10000
        else:
            best_score = 0

        training_args = {
            'dropout': self.dropout,
            'use_crf': self.use_crf,
            'ignore_index': self.ignore_index,
            'intent_loss_coef': self.intent_loss_coef,
            'tag_loss_coef': self.tag_loss_coef,
            'intent_labels': self.intent_labels,
            'tag_labels': self.tag_labels,
            'max_seq_len': self.max_seq_len,
            'tokenizer_name': self.tokenizer_name,
            'use_intent_context_concat': self.use_intent_context_concat,
            'use_intent_context_attention': self.use_intent_context_attention,
            'attention_embedding_dim': self.attention_embedding_dim, 
            'intent_embedding_type': self.intent_embedding_type,
            'use_attention_mask': self.use_attention_mask
        }
        
        for _ in train_iterator:
            epoch_iterator = tqdm(train_dataloader, desc='Iteration')
            for step, batch in enumerate(epoch_iterator):
                self.model.train()
                batch = tuple(t.to(self.device) for t in batch)

                inputs = {
                    'input_ids': batch[0],
                    'attention_mask': batch[1],
                    'token_type_ids': batch[2], 
                    'intent_label_ids': batch[3],
                    'tag_labels_ids': batch[4]
                }

                outputs = self.model(**inputs)
                loss = outputs[0]

                if gradient_accumulation_steps > 1:
                    loss = loss / gradient_accumulation_steps

                loss.backward()
                tr_loss += loss.item()

                if (step + 1) % gradient_accumulation_steps == 0:
                    torch.nn.utils.clip_grad_norm_(self.model.parameters(), max_grad_norm)

                    optimizer.step()
                    scheduler.step() # Update learning rate schedule
                    self.model.zero_grad()
                    global_step += 1

            if monitor_test:
                results = self.evaluate(test_dataset, eval_batch_size)
                if early_stopping:
                    early_stopping('loss', results[tuning_metric], self.model, training_args, model_dir, model_name)
                    if early_stopping.early_stop:
                        logger.info(f"-"*100)
                        logger.info(f"Exiting from training early.")
                        break
            
            if save_best_model and monitor_test:
                if tuning_metric == 'loss':
                    tmp_score = -results.get(tuning_metric, 0.0)
                else:
                    tmp_score = results.get(tuning_metric, 0.0)

                if best_score < tmp_score:
                    best_score = tmp_score
                    logger.info(f"Save the best model !")
                    self.save_model(model_dir, model_name)

            if 0 < max_steps < global_step:
                epoch_iterator.close()
                break

        return global_step, tr_loss / global_step
    
    def evaluate(
        self, 
        dataset: Union[JointNLUDataset, TensorDataset], 
        batch_size: int=64, 
        view_report: bool=False
    ):
        if isinstance(dataset, JointNLUDataset):
            dataset = dataset.build_dataset()
            
        eval_sampler = SequentialSampler(dataset)
        eval_dataloader = DataLoader(dataset, sampler=eval_sampler, batch_size=batch_size)

        logger.info(f"➖➖➖➖➖ Running evaluation ➖➖➖➖➖")
        logger.info(f"Num exmaples = {len(dataset)}")
        logger.info(f"Batch size = {batch_size}")

        eval_loss = 0.0
        nb_eval_steps = 0
        intent_preds = None
        tag_preds = None
        out_intent_label_ids = None
        out_tag_labels_ids = None

        self.model.eval()

        for batch in tqdm(eval_dataloader, desc='Evaluating'):
            batch = tuple(t.to(self.device) for t in batch)
            with torch.no_grad():
                inputs = {
                    'input_ids': batch[0],
                    'attention_mask': batch[1],
                    'token_type_ids': batch[2],
                    'intent_label_ids': batch[3],
                    'tag_labels_ids': batch[4]
                }

                outputs = self.model(**inputs)
                tmp_eval_loss, (intent_logits, tag_logits) = outputs[:2]
                eval_loss += tmp_eval_loss.mean().item()
            
            nb_eval_steps += 1

            # TODO: Intent prediction
            if intent_preds is None:
                intent_preds = intent_logits.detach().cpu().numpy()
                out_intent_label_ids = inputs['intent_label_ids'].detach().cpu().numpy()
            else:
                intent_preds = np.append(intent_preds, intent_logits.detach().cpu().numpy(), axis=0)
                out_intent_label_ids = np.append(
                    out_intent_label_ids, inputs['intent_label_ids'].detach().cpu().numpy(), axis=0)

            # TODO: Tag prediction
            if tag_preds is None:
                if self.use_crf:
                    tag_preds = np.array(self.model.crf.decode(tag_logits))
                else:
                    tag_preds = tag_logits.detach().cpu().numpy()

                out_tag_labels_ids = inputs["tag_labels_ids"].detach().cpu().numpy()
            else:
                if self.use_crf:
                    tag_preds = np.append(tag_preds, np.array(self.model.crf.decode(tag_logits)), axis=0)
                else:
                    tag_preds = np.append(tag_preds, tag_logits.detach().cpu().numpy(), axis=0)

                out_tag_labels_ids = np.append(out_tag_labels_ids, inputs["tag_labels_ids"].detach().cpu().numpy(), axis=0)

        
        eval_loss = eval_loss / nb_eval_steps

        # TODO: Intent results
        intent_label_map = {i: label for i, label in enumerate(self.intent_labels)}
        intent_preds = np.argmax(intent_preds, axis=1)
        intent_pred_list = [intent_label_map[i] for i in intent_preds]
        intent_label_list = [intent_label_map[i] for i in out_intent_label_ids]

        # TODO: Tag results
        if not self.use_crf:
            tag_preds = np.argmax(tag_preds, axis=2)

        tag_label_map = {i: label for i, label in enumerate(self.tag_labels)}
        out_tag_labels = [[] for _ in range(out_tag_labels_ids.shape[0])]
        tag_preds_list = [[] for _ in range(out_tag_labels_ids.shape[0])]

        for i in range(out_tag_labels_ids.shape[0]):
            for j in range(out_tag_labels_ids.shape[1]):
                if out_tag_labels_ids[i, j] != self.pad_token_label_id:
                    out_tag_labels[i].append(tag_label_map[out_tag_labels_ids[i][j]])
                    if tag_label_map[tag_preds[i][j]] == 'PAD':
                        tag_preds_list[i].append('O')
                    else:
                        tag_preds_list[i].append(tag_label_map[tag_preds[i][j]])

        total_results = compute_metrics(intent_pred_list, intent_label_list, tag_preds_list, out_tag_labels)

        results = {}
        results['loss'] = eval_loss
        results['mean_acc_score'] = total_results['mean_acc_score']
        results['intent_acc'] = total_results['intent_acc']
        results['intent_f1'] = total_results['intent_f1']
        results['intent_precision'] = total_results['intent_precision']
        results['intent_recall'] = total_results['intent_recall']
        results['tag_f1'] = total_results['tag_f1']
        results['tag_precision'] = total_results['tag_precision']
        results['tag_recall'] = total_results['tag_recall']

        logger.info(f"➖➖➖➖➖ Evaluation results ➖➖➖➖➖")

        fmt = '{:>{width}s} ' + ' {:>9.{digits}f}'
        for key in results.keys():
            result = (key, results[key])
            logger.info(fmt.format(*result, width=20, digits=4))

        if view_report:
            logger.info(f"View Intent Report:")
            print(total_results['intent_report'])

            logger.info(f"View Tags Report:")
            print(total_results['tag_report'])

        return results

    def predict(
        self, 
        sample, 
        lowercase: bool=True, 
        rm_emoji: bool=True, 
        rm_url: bool=True, 
        rm_special_token: bool=False, 
        **kwargs
    ):
        self.model.eval()

        sample = normalize(
            sample, 
            rm_emoji=rm_emoji,
            rm_url=rm_url,
            lowercase=lowercase,
            rm_special_token=rm_special_token
        )

        if len(sample) == 0:
            return None
        
        # TODO: Create a DataFrame
        dict_sample = {
            'text': [sample],
            'intent': ['UNK'],
            'tag': [' '.join(['O']*len(sample.split()))]
        }
        data_df = pd.DataFrame.from_dict(dict_sample)
        dataset = JointNLUDataset(
            mode='test',
            data_df=data_df,
            text_col='text',
            intent_col='intent',
            tag_col='tag',
            intent_labels=self.intent_labels,
            tag_labels=self.tag_labels,
            special_intents=[],
            special_tags=[],
            max_seq_len=self.max_seq_len,
            tokenizer=self.tokenizer_name
        )
        dataset = dataset.build_dataset()

        # TODO: Predict
        sampler = SequentialSampler(dataset)
        dataloader = DataLoader(dataset, sampler=sampler, batch_size=1)

        all_tag_label_mask = None
        intent_preds = None
        tag_preds = None
        self.pad_token_label_id = self.ignore_index

        for batch in tqdm(dataloader, desc='Predicting'):
            batch = tuple(t.to(self.device) for t in batch)
            with torch.no_grad():
                inputs = {
                    'input_ids': batch[0],
                    'attention_mask': batch[1],
                    'token_type_ids': batch[2],
                    'intent_label_ids': batch[3],
                    'tag_labels_ids': batch[4]
                }

                outputs = self.model(**inputs)
                _, (intent_logits, tag_logits) = outputs[:2]

                # TODO: Intent prediction
                if intent_preds is None:
                    intent_preds = intent_logits.detach().cpu().numpy()
                else:
                    intent_preds = np.append(intent_preds, intent_logits.detach().cpu().numpy(), axis=0)

                # TODO: Tag prediction
                if tag_preds is None:
                    if self.use_crf:
                        tag_preds = np.array(self.model.crf.decode(tag_logits))
                    else:
                        tag_preds = tag_logits.detach().cpu().numpy()

                    all_tag_label_mask = inputs["tag_labels_ids"].detach().cpu().numpy()
                else:
                    if self.use_crf:
                        tag_preds = np.append(tag_preds, np.array(self.model.crf.decode(tag_logits)), axis=0)
                    else:
                        tag_preds = np.append(tag_preds, tag_logits.detach().cpu().numpy(), axis=0)

                    all_tag_label_mask = np.append(all_tag_label_mask, inputs["tag_labels_ids"].detach().cpu().numpy(), axis=0)

        # TODO: Intent results
        intent_label_map = {i: label for i, label in enumerate(self.intent_labels)}
        intent_softmax = softmax(intent_preds, axis=1)
        intent_index = np.argmax(intent_preds, axis=1)[0]
        intent_score = np.max(intent_softmax, axis=1)[0]

        intent_return = {
            'name': intent_label_map[intent_index],
            'index': intent_index,
            'softmax_score': intent_score,
            'intent_logits': intent_logits,
            'intent_softmax': intent_softmax
        }

        # TODO: Tag results
        if not self.use_crf:
            tag_preds = np.argmax(tag_preds, axis=2)
        
        tag_preds_list = [[] for _ in range(tag_preds.shape[0])]
        tag_pred_logits = [[] for _ in range(tag_preds.shape[0])]

        tag_logits = tag_logits.detach().cpu().numpy()
        tag_label_map = {i: label for i, label in enumerate(self.tag_labels)}

        for i in range(tag_preds.shape[0]):
            for j in range(tag_preds.shape[1]):
                if all_tag_label_mask[i, j] != self.pad_token_label_id:
                    tag_preds_list[i].append(tag_label_map[tag_preds[i][j]])
                    tag_pred_logits[i].append(tag_logits[i][j])
        
        tag_softmax = softmax(tag_pred_logits, axis=2)
        tag_indexes = np.argmax(tag_pred_logits, axis=2)
        tag_scores = np.max(tag_softmax, axis=2)

        tag_return = {
            'tags': tag_preds_list[0],
            'tags_logits': tag_logits[0],
            'tags_index': tag_indexes[0],
            'tags_score': tag_scores[0]
        }

        outfinal = {
            'text': sample,
            'intent': intent_return,
            'tags': tag_return
        }

        return outfinal

    def convert_to_rasa_format(self, outputs):
        rasa_format_output = {}
        if not outputs:
            return {}

        words = outputs.get('text', '')
        rasa_format_output['text'] = words
        rasa_format_output['intent'] = {
            'name': outputs['intent'].get('name', 'UNK'),
            'confidence': outputs['intent'].get('softmax_score', 0.0),
            'intent_logits': outputs['intent'].get('intent_logits', None)
        }

        # get index start words
        ids = [0]
        temp = 0
        words = outputs.get('text').split()
        tags = outputs['tags'].get('tags', [])
        tags_probs = outputs['tags'].get('tags_score', [])

        for i in range(1, len(words)):
            ids.append(temp + len(words[i-1]) + 1)
            temp = ids[-1]

        ids.append(len(rasa_format_output["text"]) + 1)

        entities = []
        start = 0
        entity = None
        end = 0
        ping = False

        for i in range(len(tags)):
            if ping == True:
                if tags[i] == 'O':
                    end = i
                    entities.append({
                        'entity': entity, 
                        'start': ids[start], 
                        'end': ids[end] - 1,                     
                        'value': ' '.join(words[start:end]).strip(),
                        'confidence': np.average(tags_probs[start:end]).item(),
                        'extractor': self.__name__
                    })
                    ping = False

                elif ("B-" in tags[i]) and (i == len(tags) - 1):
                    end = i
                    entities.append({
                        'entity': entity, 
                        'start': ids[start], 
                        'end': ids[end] - 1,                     
                        'value': ' '.join(words[start:end]).strip(),
                        'confidence': np.average(tags_probs[start:end]).item(),
                        'extractor': self.__name__
                    })

                    start = i
                    end = i + 1
                    entity = tags[i][2:]

                    entities.append({
                        'entity': entity, 
                        'start': ids[start], 
                        'end': ids[end] - 1,
                        'value': ' '.join(words[start:end]).strip(),
                        'confidence': np.average(tags_probs[start:end]).item(),
                        'extractor': self.__name__
                    })

                elif "B-" in tags[i]:
                    end = i
                    entities.append({
                        'entity': entity, 
                        'start': ids[start], 
                        'end': ids[end] - 1,                     
                        'value': ' '.join(words[start:end]).strip(),
                        'confidence': np.average(tags_probs[start:end]).item(),
                        'extractor': self.__name__
                    })
                    ping = True
                    start = i
                    entity = tags[i][2:]

                elif i == len(tags) - 1:
                    end = i + 1
                    entities.append({
                        'entity': entity, 
                        'start': ids[start], 
                        'end': ids[end] - 1,
                        'value': ' '.join(words[start:end]).strip(),
                        'confidence': np.average(tags_probs[start:end]).item(),
                        'extractor': self.__name__
                    })

            else:
                if "B-" in tags[i] and i == len(tags) - 1:
                    start = i
                    end = i + 1
                    entity = tags[i][2:]
                    entities.append({
                        'entity': entity, 
                        'start': ids[start], 
                        'end': ids[end] - 1,
                        'value': ' '.join(words[start:end]).strip(),
                        'confidence': np.average(tags_probs[start:end]).item(),
                        'extractor': self.__name__
                    })

                elif "B-" in tags[i]:
                    start = i
                    entity = tags[i][2:]
                    ping = True

        rasa_format_output["entities"] = entities

        return rasa_format_output

    def process(
        self, 
        sample, 
        lowercase: bool=True, 
        rm_emoji: bool=True, 
        rm_url: bool=True, 
        rm_special_token: bool=False,
        **kwargs
    ):
        """Return the results same as the output of rasa format.

        :param sample: The sample need to inference
        :param lowercase: If True, lowercase data
        :param rm_emoji: If True, replace the emoji token into <space> (" ")
        :param rm_url: If True, replace the url token into <space> (" ")
        :param rm_special_token: If True, replace the special token into <space> (" "),
                                 special token included of punctuation token, characters without vietnamese characters

        :returns: The results format rasa. Example: \n
                    { \n
                        "intent": { \n
                            "name": query_kb, \n
                            "confidence": 0.9999999\n
                            }, \n
                        "entities": [\n
                           {\n
                                'confidence': 0.9994359612464905,\n
                                'end': 3,\n
                                'entity': 'object_type',\n
                                'extractor': 'OnetNet',\n
                                'start': 0,\n
                                'value': 'cũi'\n
                            }, \n
                        ],\n
                        "text": cũi này còn k sh\n
                     }\n
        """

        self.model.eval()

        outputs = self.predict(
            sample=sample, lowercase=lowercase, rm_emoji=rm_emoji, 
            rm_url=rm_url, rm_special_token=rm_special_token
        )
        rasa_format_output = self.convert_to_rasa_format(outputs)
        
        return rasa_format_output

    def save_model(self, model_dir, model_name):
        model_path = os.path.join(model_dir, model_name)
        if not os.path.exists(model_path):
            os.makedirs(model_path)
        
        model_to_save = self.model.module if hasattr(self.model, 'module') else self.model
        model_to_save.save_pretrained(model_path)

        # TODO: Save training arguments togethor with the trained model
        torch.save(
            {
                'dropout': self.dropout,
                'intent_labels': self.intent_labels,
                'tag_labels': self.tag_labels,
                'use_crf': self.use_crf,
                'ignore_index': self.ignore_index,
                'use_intent_context_concat': self.use_intent_context_concat,
                'use_intent_context_attention': self.use_intent_context_attention,
                'attention_embedding_dim': self.attention_embedding_dim, 
                'max_seq_len': self.max_seq_len,
                'intent_embedding_type': self.intent_embedding_type,
                'use_attention_mask': self.use_attention_mask,
                'intent_loss_coef': self.intent_loss_coef,
                'tag_loss_coef': self.tag_loss_coef,
                'tokenizer_name': self.tokenizer_name
            },
            os.path.join(model_path, 'training_args.bin')
        )

        logger.info(f"Saving model checkpoint to {model_path}")

    def load_model(self, model_path: str=None):
        if not os.path.exists(model_path):
            raise Exception(f"Model path: `{model_path}` doesn't exists or broken !")
        
        try:
            checkpoint = torch.load(os.path.join(model_path, 'training_args.bin'))
            self.dropout = checkpoint.get('dropout')
            self.use_crf = checkpoint.get('use_crf')
            self.ignore_index = checkpoint.get('ignore_index')
            self.use_intent_context_concat = checkpoint.get('use_intent_context_concat')
            self.use_intent_context_attention = checkpoint.get('use_intent_context_attention')
            self.attention_embedding_dim = checkpoint.get('attention_embedding_dim')
            self.max_seq_len = checkpoint.get('max_seq_len')
            self.intent_embedding_type = checkpoint.get('intent_embedding_type')
            self.use_attention_mask = checkpoint.get('use_attention_mask')
            self.intent_loss_coef = checkpoint.get('intent_loss_coef', 1.0)
            self.tag_loss_coef = checkpoint.get('tag_loss_coef', 1.0)
            self.intent_labels = checkpoint.get('intent_labels')
            self.tag_labels = checkpoint.get('tag_labels')
            self.max_seq_len = checkpoint.get('max_seq_len')
            self.tokenizer_name = checkpoint.get('tokenizer_name')

            self.model = self.model_class.from_pretrained(
                model_path,
                config=self.config,
                intent_labels=self.intent_labels,
                tag_labels=self.tag_labels,
                dropout=self.dropout,
                use_crf=self.use_crf,
                ignore_index=self.ignore_index,
                use_intent_context_concat=self.use_intent_context_concat,
                use_intent_context_attention=self.use_intent_context_attention,
                attention_embedding_dim=self.attention_embedding_dim, 
                max_seq_len=self.max_seq_len,
                intent_embedding_type=self.intent_embedding_type,
                use_attention_mask=self.use_attention_mask,
                intent_loss_coef=self.intent_loss_coef,
                tag_loss_coef=self.tag_loss_coef,
            )

            self.model.to(self.device)
            logger.info(f"Model Loaded from path: `{model_path}` !")

        except:
            raise Exception(f"Some model files from model path: `{model_path}` might be missing...")
