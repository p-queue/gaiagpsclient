import logging
import random
import re
import textwrap

from gaiagps.shell import command
from gaiagps.shell import options
from gaiagps import util


class Area(command.Command):
    """Manage areas

    This command allows you to take action on areas, such as adding,
    removing, and renaming them.
    """

    _editable_properties = ('color', 'notes', 'public', 'title', 'revision',
                            'activities')

    @staticmethod
    def opts(parser):
        cmds = parser.add_subparsers(dest='subcommand')

        colorize = cmds.add_parser(
            'colorize', help='Colorize areas',
            description=('Colorize areas in various ways. This will allow '
                         'colorizing areas in various ways, including '
                         'attempting to match areas in a GPX file and '
                         'changing the GaiaGPS color to match.'))
        colorize.add_argument('name', nargs='*',
                              help='Name (or ID)')
        colorize.add_argument('--in-folder', metavar='FOLDER',
                              help='Only affect items in this folder')
        colorize.add_argument('--match', action='store_true',
                              help=('Treat names as regular expressions and '
                                    'include all matches'))
        colorize.add_argument('--random', action='store_true',
                              help='Randomly colorize selected areas')
        colorize.add_argument('--from-gpx-file', metavar='FILE',
                              help=('Attempt to colorize areas to match '
                                    'corresponding data in a GPX file'))
        colorize.add_argument('--dry-run', action='store_true',
                              help=('Do not actually change colors. It is '
                                    'HIGHLY recommended that you use this '
                                    'to validate an approach before allowing '
                                    'changes to be made!'))
        colorize.add_argument('--color',
                              help=('Change matching areas to this color. '
                                    'Provide an HTML color code '
                                    '(like #FBABCD).'))

        options.edit_ops(cmds)
        options.remove_ops(cmds, 'area')
        options.rename_ops(cmds)
        options.move_ops(cmds)
        options.export_ops(cmds)
        options.list_and_dump_ops(cmds)
        options.archive_ops(cmds)
        options.show_ops(cmds)

    def _rev_match(self, server, local):
        srev = server['features'][0]['properties']['revision']
        try:
            lrev = local['features'][0]['properties']['revision']
        except KeyError:
            lrev = None
        if srev != lrev:
            logging.getLogger('area').debug(
                'Server revision is %r, local is %r' % (
                    srev, lrev))
            raise Exception(
                ('%s has changed on the server or '
                 'lists are out of sync; unable to apply changes') % (
                    server['features'][0]['properties']['title']))

    def _edit_preamble(self):
        return (super(Area, self)._edit_preamble() +
                textwrap.wrap('Available colors are: ' +
                              ' '.join(util.COLOR_ALIASES.keys())))

    def _edit_preprocess(self, obj):
        color_rev = {v: k for k, v in util.COLOR_ALIASES.items()}
        props = {k: obj['features'][0]['properties'][k]
                 for k in self._editable_properties}
        props['color'] = color_rev.get(props['color'].upper(), props['color'])
        props['id'] = obj['id']
        obj['features'][0]['properties'] = props
        return obj

    def _edit_postprocess(self, obj):
        # For some reason areas are different than waypoints. The PUT
        # seems to expect just a dict of properties. We include id here
        # because apiclient expects it for logging, but otherwise just
        # dump the properties into the payload.
        color_fwd = util.COLOR_ALIASES
        props = obj['features'][0]['properties']
        # For some reason the server changes the colors slightly on upload.
        # The COLOR_ALIASES are the ones defined in the web app.
        props['color'] = color_fwd.get(props['color'].lower(),
                                       props['color'])
        return dict({k: v for k, v in props.items()
                     if k in self._editable_properties},
                    id=obj['id'])

    def edit(self, args):
        editable = ['id'] + \
            ['features/0/properties/%s' % p for p in self._editable_properties]
        return self._edit(args, editable)

    def _colorize_areas_by_id(self, dry_run, areas):
        for (area_name, area_id), color_code in areas.items():
            self.verbose('Coloring area %r %r' % (area_name, color_code))
            if dry_run:
                continue
            if not self.client.put_object('area',
                                          {'id': area_id,
                                           'color': color_code}):
                raise RuntimeError('Failed to set area %r to %r' % (
                    area_id, color_code))

    def colorize(self, args):
        try:
            objs = self.find_objects(args.name, match=args.match)
        except command._Safety:
            objs = []

        if args.name and not objs:
            # Some query was provided but we found nothing. Run no further.
            print('No matching objects to colorize')
            return 1

        if args.in_folder:
            folder_id = self.get_object(args.in_folder, objtype='folder')['id']
        else:
            folder_id = None

        def only_folder(objs):
            return [o for o in objs
                    if folder_id is None or o['folder'] == folder_id]

        if args.random:
            colors = list(util.COLOR_ALIASES.values())
            self._colorize_areas_by_id(
                args.dry_run,
                {(t['title'], t['id']): random.choice(colors)
                 for t in only_folder(objs)})
        elif args.from_gpx_file:
            gpx_areas = util.get_area_colors_from_gpx(args.from_gpx_file)
            to_change = {}

            if not gpx_areas:
                print('No colored areas found in %r' % args.from_gpx_file)
                return 1

            if not objs:
                # No names/ids specified, so try to look up everything
                # in the GPX file. This is not very efficient, but alas.
                objs = self.find_objects(gpx_areas.keys(), allow_missing=True)
                self.verbose(
                    'Looked up %i areas from %i found in GPX file' % (
                        len(objs), len(gpx_areas)))

            if not objs:
                print('No matching objects to colorize')
                return 1

            for obj in only_folder(objs):
                if obj['title'] in gpx_areas:
                    to_change[(obj['title'], obj['id'])] = (
                        util.COLOR_ALIASES[
                            util.GPXX_COLORS_TO_GAIA[
                                gpx_areas[obj['title']]]])
                else:
                    self.verbose('Area %r not found in GPX file' % (
                        obj['title']))
            self._colorize_areas_by_id(args.dry_run, to_change)
        elif args.color:
            if not re.match('^#?[A-f0-9]{6}$', args.color):
                print('Invalid color code. Provide an HTML color like #FCEBDA')
                return 1
            if not args.color.startswith('#'):
                args.color = '#%s' % args.color
            self._colorize_areas_by_id(
                args.dry_run,
                {(o['title'], o['id']): args.color for o in only_folder(objs)})
