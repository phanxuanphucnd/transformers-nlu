[![PyPI - Python](https://img.shields.io/badge/python-py%203.6%20|py%203.7%20-blue.svg)](https://github.com/phanxuanphucnd/transformers-nlu)
[![PyPI - License](https://img.shields.io/badge/license-MIT-green.svg)](https://github.com/phanxuanphucnd/transformers-nlu/blob/main/LICENSE)
[![Open In Jupyter notebook](https://colab.research.google.com/assets/colab-badge.svg)](https://github.com/phanxuanphucnd/transformers-nlu/tree/main/tutorials)

<img src="docs/imgs/NLU-Transformers.png" width="45%" height="45%" align="right" />

## Table of contents

1. [Introduction](#introduction)
2. [How to use `arizona.nlu`](#how_to_use)
    - [Installation](#installation)
    - [Data structure](#data_structure)
    - [Example usage](#usage)

## <a name='introduction'></a> Introduction

Evolving from basic button/ menu architecture and the keyword recognition, chatbots have now entered the domain of contextual conversation. They don’t just translate but understand the speech/ text input, get smarter and sharper with every conversation and pick up on chat history and patterns. With the general advancement of linguistics, chatbots can be deployed to discern not just intents and meanings but also to better understand sentiments, sarcasm, and even tone of voice.

Natural Language Processing (NLP) is an engine for the chatbot to understand the user’s intent in the message and fetch the most appropriate response from its database. Regardless of which language a computer is learning, NLP understands the syntax, semantics, discourse, and purpose of the message to engage in a human-like conversation. 
There are three components of an NLP system - Natural Language Understanding (NLU), Dialogue Manager (DM) and Natural Language Generation (NLG). When you input a text into an NLP engine, the meaning or context of the user is deciphered by the NLP construct, navigate an action by DM and the response is generated by NLG. 

Process flow:

`User text` -> `Chatbot` -> `NLU` -> `DM` -> `NLG` -> `Response Generated.`

What is Natural Language Understanding (NLU) in Chatbot?
NLU can be used in a variety of applications including chatbots, customer service, sales, and virtual assistants, etc. Many bigcom are using NLU, e.g. Amazon uses it to help users complete purchases, Google uses it in its search engine and Netflix uses it to recommend movies or TV shows. NLU helps provide a better user experience to customers.
NLU is understanding the meaning of the user’s input. Primarily focused on machine reading comprehension, NLU gets the chatbot to comprehend what a body of text means. NLU is nothing but an understanding of the text given and classifying it into proper intents.
Intent Classification and Slot Filling (in other words, Named Entities Recognition - NER) are two essential tasks for NLU to form a semantic parse for user utterances. IC focuses on predicting the intent of the query, while NER extracts semantic concepts. 

Transformer-based models have shown significantly better performance than the previous neural networks models. Currently, Denver has provided two independent models for each IC and NER tasks, one joint model using bi-LSTM. With the outperformed results of joint Transformer-based models for NLU tasks. We propose three steps to approach the problem as follows:
- Provides a Joint model based on BERT/ RoBERTa + CRF ([Proposed approach 1](https://github.com/phanxuanphucnd/transformers-nlu/blob/main/docs/imgs/nlu%20architecture%20propose%200.png)).
- Provides Joint model based on BERT/ RoBERTa + CRF with an intent-slot attention layer to explicitly convey the intent context information via the soft intent label embedding into entity extraction ([Proposed approach 2](https://github.com/phanxuanphucnd/transformers-nlu/blob/main/docs/imgs/nlu%20architecture%20propose%201.png)). There are two mechanisms intent context embedding includes: `hard-intent_context_embedding` and `soft-intent_context_embedding`.

Named: `JointCoberta`.


**[EXPERIMENT REPORT](https://github.com/phanxuanphucnd/transformers-nlu/blob/main/docs/A%20overview%20of%20Nature%20Language%20Understanding%20(NLU)%20and%20SoTA%20Architectures%20so%20far.pdf)**

**[TUTORIAL SERIES](https://github.com/phanxuanphucnd/transformers-nlu/tree/main/tutorials)**

<!-- ### Architecture 1:

<img src="docs/imgs/nlu architecture propose 0.png" width="60%" height="60%" align="center" />

### Architecture 2:

<img src="docs/imgs/nlu architecture propose 1.png" width="60%" height="60%" align="center" /> -->

# <a name='how_to_use'></a> How to use `arizona`

### Installation <a name='installation'></a>


```js
>>> pip install dist/arizona-0.1.0-py3-none-any.whl
```

### <a name='data_structure'></a> Data Structure

The input is a .csv file contains 3 columns:

| text | intent | tags |
| ---- | ------ | ---- | 
| tôi là Phúc | inform | O O B-people |

### <a name='usage'></a> Example usage

#### 1️⃣ Training a model JointCoberta

```py
# -*- coding: utf-8 -*-
# Copyright (c) 2021 by Phuc Phan

from arizona.nlu.datasets import JointNLUDataset
from arizona.nlu.learners.joint import JointCoBERTaLearner


def test_training():

    train_dataset = JointNLUDataset(
        mode='train',
        data_path='data/train.csv',
        tokenizer='phobert',
        text_col='text',
        intent_col='intent',
        tag_col='tags',
        special_intents=["UNK"],
        special_tags=["PAD", "UNK"],
        max_seq_len=50,
        ignore_index=0,
        lowercase=True,
        rm_emoji=False,
        rm_url=False,
        rm_special_token=False,
        balance_data=False
    )

    test_dataset = JointNLUDataset(
        mode='test',
        data_path='data/kcloset/test.csv',
        tokenizer='phobert',
        text_col='text',
        intent_col='intent',
        tag_col='tags',
        intent_labels=train_dataset.intent_labels,
        tag_labels=train_dataset.tag_labels,
        special_intents=["UNK"],
        special_tags=["PAD", "UNK"],
        max_seq_len=50,
        ignore_index=0,
        lowercase=True,
        rm_emoji=False,
        rm_url=False,
        rm_special_token=False,
        balance_data=False
    )

    learner = JointCoBERTaLearner(
        model_type='phobert',
        model_name_or_path='vinai/phobert-base', 
        intent_loss_coef=0.4, 
        tag_loss_coef=0.6,
        use_intent_context_concat=False,
        use_intent_context_attention=True,
        attention_embedding_dim=200,
        max_seq_len=50,
        intent_embedding_type='soft',
        use_attention_mask=False,
        # device='cpu'
    )
    learner.train(
        train_dataset,
        test_dataset,
        train_batch_size=32,
        eval_batch_size=64,
        learning_rate=4e-5,
        n_epochs=100,
        view_model=True,
        monitor_test=True,
        save_best_model=True,
        model_dir='./models',
        model_name='phobert.nlu',
        gpu_id=0
    )

test_training()

```

#### 2️⃣ Evaluate the model JointCoberta

```py
# -*- coding: utf-8 -*-
# Copyright (c) 2021 by Phuc Phan

from arizona.nlu.datasets import JointNLUDataset
from arizona.nlu.learners.joint import JointCoBERTaLearner


def test_evaluate():

    test_path = 'data/test.csv'
    model_path = 'models/phobert.nlu'

    learner = JointCoBERTaLearner(model_type='phobert')
    learner.load_model(model_path)
    
    test_dataset = JointNLUDataset(
        mode='test',
        data_path=test_path,
        tokenizer='coberta',
        text_col='text',
        intent_col='intent',
        tag_col='tags',
        intent_labels=learner.intent_labels,
        tag_labels=learner.tag_labels,
        special_intents=["UNK"],
        special_tags=["PAD", "UNK"],
        max_seq_len=50,
        ignore_index=0,
        lowercase=True,
        rm_emoji=False,
        rm_url=False,
        rm_special_token=False,
        balance_data=False
    )

    out = learner.evaluate(test_dataset, batch_size=256, view_report=True)

test_evaluate()
```

#### 3️⃣ Inference a given sample

```py
# -*- coding: utf-8 -*-
# Copyright (c) 2021 by Phuc Phan

from arizona.nlu.datasets import JointNLUDataset
from arizona.nlu.learners.joint import JointCoBERTaLearner


def test_infer():

    text = 'áo này giá bnh tiền'
    model_path = 'models/phobert.nlu'

    learner = JointCoBERTaLearner(model_type='phobert')
    learner.load_model(model_path)
    output = learner.predict(
        sample=text,
        lowercase=True,
        rm_emoji=True,
        rm_url=True,
        rm_special_token=True
    )

    rasa_format_output = learner.process(
        sample=text,
        lowcase=True,
        rm_emoji=True,
        rm_url=True,
        rm_special_token=True
    )

    from pprint import pprint
    print("\n>>>>> Output function predict(): ")
    pprint(output)

    print("\n>>>>> Output function process(): ")
    pprint(rasa_format_output)

test_infer()
```



## License

```
MIT License

Copyright (c) 2021 Phuc Phan

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```
  
## Author

📌: 

``arizona`` was developed by Phuc Phan © Copyright 2021.

For any questions or comments, please contact the following email: phanxuanphucnd@gmail.com.

If you find it useful, please give me 1 star 🌟. 

Thank you for your interesting to ``arizona``! 🤗
