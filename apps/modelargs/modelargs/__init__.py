import argparse
import json
import sys

def resolve_type(value):
    if choices := value.get('enum'):
        args = dict(choices=choices)
        if len(choices) > 0 and type(choices[0]) == int:
            args['type'] = int
        return args
    else:
        if not (typ := value.get('type')):
            raise ValueError(f'Invalid model schema: property `{k}` missing attribute `type`')
        if typ == 'number':
            def number(x):
                try:
                    return int(x)
                except ValueError:
                    return float(x)
            return dict(type=number)
        elif typ == 'integer':
            return dict(type=int)
        elif typ == 'string':
            return {}
        elif typ == 'boolean':
            def boolean(x):
                if x in ('true', 'True'):
                    return True
                elif x in ('false', 'False'):
                    return False
                else:
                    raise ValueError(f'Invalid boolean value for property `{k}`')
            return dict(type=boolean)
        elif typ == 'array':
            if not (item := value.get('items')):
                raise ValueError(f'Array type is missing property `items`')
            return { **resolve_type(item), 'nargs': '+' }
        else:
            raise ValueError(f'Property `{k}` has unexpected type: {v["type"]}`')

def parse(schema, stage=None):
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

        if (required := v.get('required')) and stage in required:
            args['required'] = True

        args.update(resolve_type(v))
        parser.add_argument('--' + k, **args)

    try:
        i = sys.argv.index('--')
        sys.argv, args = sys.argv[:i], sys.argv[i+1:]
    except:
        args = sys.argv[1:]

    return vars(parser.parse_args(args))

def emit_action(kind, value):
    print(f'Toolbox:{kind}: {value}', flush=True)
