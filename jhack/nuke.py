import asyncio
from dataclasses import dataclass
from subprocess import Popen, PIPE
from typing import Optional, Literal, List, Callable

from jhack.config import JUJU_COMMAND
from jhack.helpers import juju_status, juju_models, current_model
import typer

from multiprocessing.pool import ThreadPool

from jhack.logger import logger


@dataclass
class Rel:
    provider: str
    requirer: str


@dataclass
class Nukeable:
    name: str
    type: Literal['model', 'app', 'relation']
    obj: Rel = None


def _get_models(filter_):
    """List of existing models."""
    models = juju_models()
    found = 0
    _models = []
    for line in models.split('\n'):
        if line.startswith('Model '):
            found = 1
        if found:
            model_name = line.split()[0]
            if filter_(model_name):
                _models.append(model_name)
    _models.remove('controller')  # shouldn't try to nuke that one!
    return tuple(Nukeable(m, 'model') for m in _models)


def _get_apps_and_relations(model: Optional[str],
                            borked: bool,
                            filter_: Callable[[str], bool]) -> List[Nukeable]:
    status = juju_status("", model)
    apps = 0
    relation = 0
    nukeables = []
    for line in status.split('\n'):
        if line.startswith('App '):
            apps = 1
        if line.startswith('Relation '):
            relation = 1
            app = 0

        if apps:
            if borked and 'active' in line:
                continue
            app_name = line.split()[0].strip('*')
            if filter_(app_name):
                nukeables.append(Nukeable(app_name, 'app'))

        if relation:
            prov, req, _ = line.split()
            rel = Rel(prov.strip(), req.strip())
            if filter_(rel.provider) or filter_(rel.requirer):
                nukeables.append(Nukeable(f'{prov} {req}', 'relation', obj=rel))

    return nukeables


def _gather_nukeables(obj: str, model: Optional[str], borked: bool):
    globber = lambda x: True
    if obj.startswith('*'):
        globber = lambda s: s.endswith(obj.strip('*'))
    elif obj.endswith('*'):
        globber = lambda s: s.startswith(obj.strip('*'))
    obj = obj.strip('*')

    nukeables: List[Nukeable] = []

    if model:
        nukeables.extend(_get_apps_and_relations(model, borked=borked, filter_=globber))

    if not model:
        models = _get_models(filter_=globber)
        for _model in models:
            nukeables.extend(_get_apps_and_relations(_model.name, borked=borked, filter_=globber))
            nukeables.append(_model)
    return nukeables


def _nuke(obj: Optional[str], model: Optional[str], borked: bool, dry_run: bool):
    if obj is None:
        nukeables = [Nukeable(current_model(), 'model')]
    else:
        nukeables = _gather_nukeables(obj, model, borked=borked)
    nukes = []
    for nukeable in nukeables:
        if nukeable.type == 'model':
            nukes.append(f"{JUJU_COMMAND} destroy-model {nukeable.name} "
                         f"--force --no-wait --destroy-storage")

        elif nukeable.type == 'app':
            nukes.append(f"{JUJU_COMMAND} remove-application {nukeable.name} "
                         f"--force --no-wait")

        elif nukeable.type == 'relation':
            nukes.append(
                f"{JUJU_COMMAND} remove-relation {nukeable.obj.provider} "
                f"{nukeable.obj.requirer}")

        else:
            raise ValueError(nukeable.type)

    # defcon 5
    if dry_run:
        for nukeable in nukeables:
            print(f'would nuke {nukeable.name} ⚛')

        print("✞ RIP ✞")
        return

    def fire(nukeable: Nukeable, nuke: str):
        print(f'nuking :: {nukeable.name} ⚛')
        logger.debug(f'nuking {nukeable} with {nuke}')
        proc = Popen(nuke.split(' '), stdout=PIPE, stderr=PIPE)
        while proc.returncode is None:
            asyncio.sleep(1)
        if proc.returncode != 0:
            print(f'something went wrong nuking {nukeable.name};'
                  f'stdout={proc.stdout.read().decode("utf-8")}'
                  f'stderr={proc.stderr.read().decode("utf-8")}')
        else:
            logger.debug(f'hit and sunk')

    tp = ThreadPool()
    for nukeable, nuke in zip(nukeables, nukes):
        tp.apply_async(fire, (nukeable, nuke))

    tp.close()
    tp.join()

    print('All done.')


def nuke(*what: str,
         model: Optional[str] = typer.Option(
             None, '-m', '--model',
             help='The model. Defaults to current model.'),
         borked: bool = typer.Option(
             None, '-b', '--borked',
             help='Nukes all borked applications.'),
         dry_run: bool = typer.Option(
             None, '--dry-run',
             help='Do nothing, print out what would have happened.')):
    """Surgical carpet bombing tool.

    Attempts to guess what you want to burn, and vanquishes it for you.
    """
    if not what:
        _nuke(None, model=model, borked=borked, dry_run=dry_run)
    for obj in what:
        _nuke(obj, model=model, borked=borked, dry_run=dry_run)


if __name__ == '__main__':
    nuke(
        model=None,
        borked=False,
        dry_run=True)