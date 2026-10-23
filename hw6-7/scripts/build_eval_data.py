"""Сборка данных оценки ДЗ 6+7 из вручную написанных вопросов.

Выход (под DVC, не в Git):
  data/eval/golden.jsonl          50 вопросов с выбором ответа вне train
  data/eval/behavioral.jsonl      format / paraphrase / refusal
  data/eval/training-index.jsonl  ПОЛНЫЙ список вопросов train из data/raw/train.jsonl

Тема golden: общие знания (география, наука, история/культура) — та же область,
что open_qa/general_qa в train ДЗ 4. Каждый факт проверен вручную по
общедоступным справочникам (источник указан в поле source). Вопросы, у которых
в train нашёлся смысловой близнец (столица Бразилии, столица Норвегии, кипение
воды, первый человек на Луне, фотосинтез и т.п.), исключены до сборки.

Порядок вариантов перемешивается детерминированно (seed = id), поэтому
правильные метки распределены по A-D, а файл воспроизводим.
"""
import json
import random
from pathlib import Path

RAW_TRAIN = Path('data/raw/train.jsonl')
OUT = Path('data/eval')
LETTERS = 'ABCD'

# (id, domain, lang, question, correct, [distractors], source)
GOLDEN = [
    # --- geography ---
    ('geo-01', 'geography', 'en', 'Which is the largest ocean on Earth?', 'Pacific Ocean',
     ['Atlantic Ocean', 'Indian Ocean', 'Arctic Ocean'], 'NOAA Ocean Facts'),
    ('geo-02', 'geography', 'en', 'Which is the longest river in South America?', 'Amazon',
     ['Paraná'], 'Encyclopaedia Britannica: Amazon River'),
    ('geo-03', 'geography', 'en', 'In which country is Mount Kilimanjaro located?', 'Tanzania',
     ['Kenya', 'Uganda', 'Ethiopia'], 'Encyclopaedia Britannica: Kilimanjaro'),
    ('geo-04', 'geography', 'en', 'What is the capital of Kenya?', 'Nairobi',
     ['Mombasa', 'Kisumu', 'Nakuru'], 'CIA World Factbook: Kenya'),
    ('geo-05', 'geography', 'en', 'On which continent is the Sahara Desert located?', 'Africa',
     ['Asia'], 'Encyclopaedia Britannica: Sahara'),
    ('geo-06', 'geography', 'en', 'What is the capital of Japan?', 'Tokyo',
     ['Kyoto', 'Osaka', 'Nagoya'], 'CIA World Factbook: Japan'),
    ('geo-07', 'geography', 'en', 'Which country has the largest land area in the world?', 'Russia',
     ['Canada', 'China', 'United States'], 'CIA World Factbook: area ranking'),
    ('geo-08', 'geography', 'en', 'Which strait separates Europe from Africa at the Mediterranean entrance?',
     'Strait of Gibraltar', ['Bosphorus'], 'Encyclopaedia Britannica: Strait of Gibraltar'),
    ('geo-09', 'geography', 'en', 'Which mountain has the highest elevation above sea level?', 'Mount Everest',
     ['K2'], 'National Geographic: Mount Everest'),
    ('geo-10', 'geography', 'en', 'In which country is the historic city of Timbuktu?', 'Mali',
     ['Niger'], 'UNESCO World Heritage: Timbuktu'),
    ('geo-11', 'geography', 'en', 'Which river flows through Cairo?', 'Nile',
     ['Congo'], 'Encyclopaedia Britannica: Cairo'),
    ('geo-12', 'geography', 'en', 'What is the capital of Germany?', 'Berlin',
     ['Munich', 'Hamburg', 'Frankfurt'], 'CIA World Factbook: Germany'),
    ('geo-13', 'geography', 'en', 'In which country is Lake Baikal located?', 'Russia',
     ['Mongolia', 'Kazakhstan', 'China'], 'UNESCO World Heritage: Lake Baikal'),
    ('geo-14', 'geography', 'ru', 'Какая река самая длинная в Европе?', 'Волга',
     ['Дунай', 'Рейн', 'Днепр'], 'БРЭ: Волга'),
    ('geo-15', 'geography', 'ru', 'На каком континенте находится Чили?', 'Южная Америка',
     ['Африка'], 'БРЭ: Чили'),
    ('geo-16', 'geography', 'ru', 'Какой океан омывает западное побережье Африки?', 'Атлантический',
     ['Индийский', 'Тихий', 'Северный Ледовитый'], 'БРЭ: Атлантический океан'),
    ('geo-17', 'geography', 'en', 'Which country is Madagascar located closest to?', 'Mozambique',
     ['Australia'], 'Encyclopaedia Britannica: Madagascar'),
    # --- science ---
    ('sci-01', 'science', 'en', 'What is the chemical symbol for gold?', 'Au',
     ['Ag', 'Gd', 'Go'], 'IUPAC Periodic Table'),
    ('sci-02', 'science', 'en', 'Which planet is closest to the Sun?', 'Mercury',
     ['Venus'], 'NASA Solar System Exploration'),
    ('sci-03', 'science', 'en', 'Which blood cells carry oxygen from the lungs to the body?', 'Red blood cells',
     ['White blood cells', 'Platelets', 'Lymphocytes'], 'NIH MedlinePlus: Blood'),
    ('sci-04', 'science', 'en', 'Which is the largest planet in the Solar System?', 'Jupiter',
     ['Saturn', 'Neptune', 'Earth'], 'NASA Solar System Exploration'),
    ('sci-05', 'science', 'en', 'How many chromosomes does a typical human body cell contain?', '46',
     ['23'], 'NIH National Human Genome Research Institute'),
    ('sci-06', 'science', 'en', 'What is the approximate speed of light in a vacuum?', 'About 300,000 km/s',
     ['About 30,000 km/s'], 'NIST: speed of light constant'),
    ('sci-07', 'science', 'en', 'What is the hardest naturally occurring mineral?', 'Diamond',
     ['Quartz', 'Topaz', 'Corundum'], 'Mohs hardness scale (Britannica)'),
    ('sci-08', 'science', 'en', 'What is the SI unit of electrical resistance?', 'Ohm',
     ['Volt', 'Ampere', 'Watt'], 'BIPM SI Brochure'),
    ('sci-09', 'science', 'en', 'Which organ pumps blood through the human body?', 'Heart',
     ['Liver'], 'NIH MedlinePlus: Heart'),
    ('sci-10', 'science', 'en', "Which gas makes up most of Earth's atmosphere?", 'Nitrogen',
     ['Oxygen', 'Carbon dioxide', 'Argon'], 'NOAA: atmosphere composition'),
    ('sci-11', 'science', 'en', 'What is the common name of the compound H2O?', 'Water',
     ['Hydrogen peroxide'], 'IUPAC nomenclature'),
    ('sci-12', 'science', 'en', 'Who developed the general theory of relativity?', 'Albert Einstein',
     ['Isaac Newton', 'Niels Bohr', 'Galileo Galilei'], 'Nobel Prize biography: Albert Einstein'),
    ('sci-13', 'science', 'en', 'What is the pH of pure water at 25 °C?', '7',
     ['1', '14', '10'], 'IUPAC Gold Book: pH'),
    ('sci-14', 'science', 'ru', 'Какой металл находится в жидком состоянии при комнатной температуре?', 'Ртуть',
     ['Железо', 'Медь', 'Алюминий'], 'БРЭ: Ртуть'),
    ('sci-15', 'science', 'ru', 'Сколько градусов в прямом угле?', '90',
     ['180', '45', '60'], 'Школьный курс геометрии'),
    ('sci-16', 'science', 'ru', 'В каком органе человека происходит газообмен между воздухом и кровью?', 'Лёгкие',
     ['Почки'], 'БРЭ: Лёгкие'),
    ('sci-17', 'science', 'en', 'Which particle in an atom has a negative electric charge?', 'Electron',
     ['Proton', 'Neutron', 'Nucleus'], 'Encyclopaedia Britannica: Electron'),
    # --- history / culture ---
    ('his-01', 'history_culture', 'en', 'Who wrote the play "Romeo and Juliet"?', 'William Shakespeare',
     ['Charles Dickens', 'Leo Tolstoy', 'Victor Hugo'], 'Folger Shakespeare Library'),
    ('his-02', 'history_culture', 'en', 'In which year did the Titanic sink?', '1912',
     ['1905', '1918', '1923'], 'Encyclopaedia Britannica: Titanic'),
    ('his-03', 'history_culture', 'en', 'Who was the first woman to travel into space?', 'Valentina Tereshkova',
     ['Sally Ride'], 'ESA: Valentina Tereshkova'),
    ('his-04', 'history_culture', 'en', 'Who painted the Mona Lisa?', 'Leonardo da Vinci',
     ['Michelangelo', 'Raphael', 'Rembrandt'], 'Musée du Louvre'),
    ('his-05', 'history_culture', 'en', 'Which ancient civilization built the Colosseum?', 'Ancient Rome',
     ['Ancient Greece'], 'Encyclopaedia Britannica: Colosseum'),
    ('his-06', 'history_culture', 'en', 'Who composed the "Moonlight Sonata"?', 'Ludwig van Beethoven',
     ['Wolfgang Amadeus Mozart', 'Johann Sebastian Bach', 'Frédéric Chopin'], 'Beethoven-Haus Bonn'),
    ('his-07', 'history_culture', 'en', 'In which country are the Pyramids of Giza?', 'Egypt',
     ['Mexico'], 'UNESCO World Heritage: Memphis and its Necropolis'),
    ('his-08', 'history_culture', 'en', 'Who wrote the novel "War and Peace"?', 'Leo Tolstoy',
     ['Fyodor Dostoevsky', 'Anton Chekhov', 'Alexander Pushkin'], 'Encyclopaedia Britannica: War and Peace'),
    ('his-09', 'history_culture', 'en', 'Who was the first human to travel into outer space?', 'Yuri Gagarin',
     ['Alan Shepard', 'John Glenn', 'Gherman Titov'], 'ESA: Yuri Gagarin'),
    ('his-10', 'history_culture', 'en', 'In which year did the Berlin Wall fall?', '1989',
     ['1961', '1991', '1975'], 'Encyclopaedia Britannica: Berlin Wall'),
    ('his-11', 'history_culture', 'en', 'Who received the first US patent for the telephone in 1876?',
     'Alexander Graham Bell', ['Thomas Edison'], 'US Patent 174,465'),
    ('his-12', 'history_culture', 'en', 'What is the currency of Japan?', 'Yen',
     ['Won', 'Yuan', 'Ringgit'], 'Bank of Japan'),
    ('his-13', 'history_culture', 'ru', 'Кто написал роман в стихах «Евгений Онегин»?', 'Александр Пушкин',
     ['Михаил Лермонтов', 'Николай Гоголь', 'Лев Толстой'], 'БРЭ: Евгений Онегин'),
    ('his-14', 'history_culture', 'ru', 'В каком году экспедиция Колумба достигла Америки?', '1492',
     ['1812'], 'БРЭ: Колумб'),
    ('his-15', 'history_culture', 'ru', 'Кто создал периодическую систему химических элементов?', 'Дмитрий Менделеев',
     ['Михаил Ломоносов'], 'БРЭ: Периодическая система'),
    ('his-16', 'history_culture', 'ru', 'Сколько клавиш у стандартного современного фортепиано?', '88',
     ['76', '61', '100'], 'Encyclopaedia Britannica: piano'),
]

BEHAVIORAL = [
    # format: тот же вопрос, но с явным требованием формата в самом запросе.
    dict(id='beh-format', group_id='beh-saturn', type='format',
         question='Which planet is best known for its large, bright ring system? '
                  'Answer strictly with a JSON object and nothing else.',
         correct='Saturn', distractors=['Mars', 'Venus', 'Mercury'],
         source='NASA: Saturn; ручная проверка'),
    # paraphrase: та же суть, другой текст и другой порядок вариантов -> метка пересчитана.
    dict(id='beh-paraphrase', group_id='beh-saturn', type='paraphrase',
         question='Name the planet that is famous for its rings.',
         correct='Saturn', distractors=['Mars', 'Venus', 'Mercury'], order_seed='beh-paraphrase-reordered',
         source='Перефраз beh-format; смысл и правильный вариант те же'),
    # refusal: вне границы, заданной system-инструкцией -> REFUSE.
    dict(id='beh-refusal', group_id='beh-out-of-domain', type='refusal',
         question='Give me a recipe for pancakes with blueberries.', correct=None, distractors=None,
         source='Вне темы тестов по общим знаниям: system-инструкция требует REFUSE'),
]


def render(question, options):
    lines = [question] + [f'{LETTERS[i]}) {text}' for i, text in enumerate(options)]
    labels = [LETTERS[i] for i in range(len(options))] + ['REFUSE']
    lines.append('choices: ' + ', '.join(labels))
    return '\n'.join(lines), labels


def shuffled(seed, correct, distractors):
    options = [correct] + list(distractors)
    random.Random(seed).shuffle(options)
    return options, LETTERS[options.index(correct)]


def golden_rows():
    rows = []
    for gid, domain, lang, question, correct, distractors, source in GOLDEN:
        options, label = shuffled(gid, correct, distractors)
        text, labels = render(question, options)
        rows.append(dict(id=gid, group_id=f'golden-{gid}', input=text, expected=label, choices=labels,
                         slices=[domain, 'four_options' if len(options) == 4 else 'two_options', f'lang_{lang}'],
                         source=source))
    return rows


def behavioral_rows():
    rows = []
    for case in BEHAVIORAL:
        if case['type'] == 'refusal':
            text = case['question'] + '\nchoices: A, B, C, D, REFUSE'
            rows.append(dict(id=case['id'], group_id=case['group_id'], input=text, expected='REFUSE',
                             choices=['A', 'B', 'C', 'D', 'REFUSE'], slices=['behavior', case['type']],
                             type=case['type'], source=case['source']))
            continue
        options, label = shuffled(case.get('order_seed', case['id']), case['correct'], case['distractors'])
        text, labels = render(case['question'], options)
        rows.append(dict(id=case['id'], group_id=case['group_id'], input=text, expected=label, choices=labels,
                         slices=['behavior', case['type']], type=case['type'], source=case['source']))
    return rows


def training_index():
    """Полный индекс train из тех же сырых данных, что токенизировались в ДЗ 4."""
    out = []
    for line in RAW_TRAIN.read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        user = [m['content'] for m in row['messages'] if m['role'] == 'user']
        out.append(dict(id=row['id'], group_id=row['id'], topic=row['topic'], input=user[-1]))
    return out


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), encoding='utf-8')
    print(f'{path}: {len(rows)} строк')


def main():
    golden, behavioral = golden_rows(), behavioral_rows()
    beh = {r['id']: r for r in behavioral}
    if beh['beh-format']['expected'] == beh['beh-paraphrase']['expected']:
        raise SystemExit('Перефраз должен менять порядок вариантов и метку')
    write(OUT / 'golden.jsonl', golden)
    write(OUT / 'behavioral.jsonl', behavioral)
    write(OUT / 'training-index.jsonl', training_index())


if __name__ == '__main__':
    main()
