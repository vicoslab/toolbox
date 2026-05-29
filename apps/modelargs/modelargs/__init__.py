import argparse
import json
import sys

def parse(schema):
    with open(schema) as f:
        j = json.load(f)
    
    if type(j) != dict:
        raise ValueError(f'Model schema `{schema}` should be an object')
    if 'type' not in j or j['type'] != 'object':
        raise ValueError('Invalid model schema: attribute `type` must be set to `object`')
    if 'properties' not in j or type(j['properties']) != dict:
        raise ValueError('Invalid model schema: attribute `properties` must be an object')
    if 'title' not in j:
        raise ValueError('Invalid model schema: missing attribute `title`')
    if 'description' not in j:
        raise ValueError('Invalid model schema: missing attribute `description`')
    
    parser = argparse.ArgumentParser(prog=j['title'], description=j['description'])

    for k, v in j['properties'].items():
        args = {}

        if description := v.get('description'):
            args['help'] = description

        if default := v.get('default'):
            args['default'] = default

        if choices := v.get('enum'):
            args['choices'] = choices
            if len(choices) > 0 and type(choices[0]) == int:
                args['type'] = int
        else:
            if 'type' not in v:
                raise ValueError(f'Invalid model schema: property `{k}` missing attribute `type`')
            elif v['type'] == 'number':
                args['type'] = float
            elif v['type'] == 'integer':
                args['type'] = int
            elif v['type'] == 'string':
                pass
            elif v['type'] == 'boolean':
                def err():
                    raise ValueError(f'Invalid boolean value for property `{k}`')
                args['type'] = lambda x: x == 'true' or not x == 'false' or err()
            else:
                raise ValueError(f'Property `{k}` has unexpected type: {v["type"]}`')

        parser.add_argument('--' + k, **args)

    try:
        i = sys.argv.index('--')
        sys.argv, args = sys.argv[:i], sys.argv[i+1:]
    except:
        args = sys.argv[1:]

    return vars(parser.parse_args(args))
