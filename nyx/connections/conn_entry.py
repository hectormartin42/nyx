"""
Connection panel entries related to actual connections to or from the system
(ie, results seen by netstat, lsof, etc).
"""

import curses

import nyx.connection_panel
import nyx.util.tracker
import nyx.util.ui_tools

from nyx.util import tor_controller
from nyx.connection_panel import Category

from stem.util import conf, connection, str_tools

try:
  # added in python 3.2
  from functools import lru_cache
except ImportError:
  from stem.util.lru_cache import lru_cache

# static data for listing format
# <src>  -->  <dst>  <etc><padding>

LABEL_FORMAT = '%s  -->  %s  %s%s'
LABEL_MIN_PADDING = 2  # min space between listing label and following data

CONFIG = conf.config_dict('nyx', {
  'features.connection.showIps': True,
  'features.connection.showExitPort': True,
  'features.connection.showColumn.fingerprint': True,
  'features.connection.showColumn.nickname': True,
  'features.connection.showColumn.destination': True,
  'features.connection.showColumn.expandedIp': True,
})


class ConnectionLine(object):
  """
  Display component of the ConnectionEntry.
  """

  def __init__(self, entry, conn, include_port=True, include_expanded_addresses=True):
    self._entry = entry
    self.connection = conn

    # includes the port or expanded ip address field when displaying listing
    # information if true

    self.include_port = include_port
    self.include_expanded_addresses = include_expanded_addresses

  def get_listing_prefix(self):
    """
    Provides a list of characters to be appended before the listing entry.
    """

    return ()

  def get_locale(self, default = None):
    """
    Provides the two letter country code for the remote endpoint.
    """

    return tor_controller().get_info('ip-to-country/%s' % self.connection.remote_address, default)

  def get_fingerprint(self, default = None):
    """
    Provides the fingerprint of this relay.
    """

    if self._entry.get_type() in (Category.OUTBOUND, Category.CIRCUIT, Category.DIRECTORY, Category.EXIT):
      my_fingerprint = nyx.util.tracker.get_consensus_tracker().get_relay_fingerprint(self.connection.remote_address, self.connection.remote_port)
      return my_fingerprint if my_fingerprint else default
    else:
      return default  # inbound connections don't have an ORPort we can resolve

  def get_nickname(self, default = None):
    """
    Provides the nickname of this relay.
    """

    nickname = nyx.util.tracker.get_consensus_tracker().get_relay_nickname(self.get_fingerprint())
    return nickname if nickname else default

  def get_listing_entry(self, width, current_time, listing_type):
    """
    Provides the tuple list for this connection's listing. Lines are composed
    of the following components:
      <src>  -->  <dst>     <etc>     <uptime> (<type>)

    Listing.IP_ADDRESS:
      src - <internal addr:port> --> <external addr:port>
      dst - <destination addr:port>
      etc - <fingerprint> <nickname>

    Listing.FINGERPRINT:
      src - localhost
      dst - <destination fingerprint>
      etc - <nickname> <destination addr:port>

    Listing.NICKNAME:
      src - <source nickname>
      dst - <destination nickname>
      etc - <fingerprint> <destination addr:port>

    Arguments:
      width       - maximum length of the line
      current_time - unix timestamp for what the results should consider to be
                    the current time
      listing_type - primary attribute we're listing connections by
    """

    # fetch our (most likely cached) display entry for the listing

    my_listing = self._get_listing_entry(width, listing_type)

    # fill in the current uptime and return the results

    time_prefix = '+' if self.connection.is_legacy else ' '

    time_label = time_prefix + '%5s' % str_tools.time_label(current_time - self.connection.start_time, 1)
    my_listing[2] = (time_label, my_listing[2][1])

    return my_listing

  @lru_cache()
  def _get_listing_entry(self, width, listing_type):
    entry_type = self._entry.get_type()

    # Lines are split into the following components in reverse:
    # init gap - " "
    # content  - "<src>  -->  <dst>     <etc>     "
    # time     - "<uptime>"
    # preType  - " ("
    # category - "<type>"
    # postType - ")   "

    line_format = nyx.util.ui_tools.get_color(nyx.connection_panel.CATEGORY_COLOR[entry_type])

    draw_entry = [(' ', line_format),
                  (self._get_listing_content(width - 19, listing_type), line_format),
                  ('      ', line_format),
                  (' (', line_format),
                  (entry_type.upper(), line_format | curses.A_BOLD),
                  (')' + ' ' * (9 - len(entry_type)), line_format)]

    return draw_entry

  @lru_cache()
  def get_details(self, width):
    """
    Provides details on the connection, correlated against available consensus
    data.

    Arguments:
      width - available space to display in
    """

    detail_format = (curses.A_BOLD, nyx.connection_panel.CATEGORY_COLOR[self._entry.get_type()])
    return [(line, detail_format) for line in self._get_detail_content(width)]

  def get_etc_content(self, width, listing_type):
    """
    Provides the optional content for the connection.

    Arguments:
      width       - maximum length of the line
      listing_type - primary attribute we're listing connections by
    """

    # for applications show the command/pid

    if self._entry.get_type() in (Category.SOCKS, Category.HIDDEN, Category.CONTROL):
      port = self.connection.local_port if self._entry.get_type() == Category.HIDDEN else self.connection.remote_port

      try:
        process = nyx.util.tracker.get_port_usage_tracker().fetch(port)
        display_label = '%s (%s)' % (process.name, process.pid) if process.pid else process.name
      except nyx.util.tracker.UnresolvedResult:
        display_label = 'resolving...'
      except nyx.util.tracker.UnknownApplication:
        display_label = 'UNKNOWN'

      if len(display_label) < width:
        return ('%%-%is' % width) % display_label
      else:
        return ''

    # for everything else display connection/consensus information

    destination_address = self.get_destination_label(26, include_locale = True)
    etc, used_space = '', 0

    if listing_type == nyx.connection_panel.Listing.IP_ADDRESS:
      if width > used_space + 42 and CONFIG['features.connection.showColumn.fingerprint']:
        # show fingerprint (column width: 42 characters)

        etc += '%-40s  ' % self.get_fingerprint('UNKNOWN')
        used_space += 42

      if width > used_space + 10 and CONFIG['features.connection.showColumn.nickname']:
        # show nickname (column width: remainder)

        nickname_space = width - used_space
        nickname_label = str_tools.crop(self.get_nickname('UNKNOWN'), nickname_space, 0)
        etc += ('%%-%is  ' % nickname_space) % nickname_label
        used_space += nickname_space + 2
    elif listing_type == nyx.connection_panel.Listing.FINGERPRINT:
      if width > used_space + 17:
        # show nickname (column width: min 17 characters, consumes any remaining space)

        nickname_space = width - used_space - 2

        # if there's room then also show a column with the destination
        # ip/port/locale (column width: 28 characters)

        is_locale_included = width > used_space + 45
        is_locale_included &= CONFIG['features.connection.showColumn.destination']

        if is_locale_included:
          nickname_space -= 28

        if CONFIG['features.connection.showColumn.nickname']:
          nickname_label = str_tools.crop(self.get_nickname('UNKNOWN'), nickname_space, 0)
          etc += ('%%-%is  ' % nickname_space) % nickname_label
          used_space += nickname_space + 2

        if is_locale_included:
          etc += '%-26s  ' % destination_address
          used_space += 28
    else:
      if width > used_space + 42 and CONFIG['features.connection.showColumn.fingerprint']:
        # show fingerprint (column width: 42 characters)
        etc += '%-40s  ' % self.get_fingerprint('UNKNOWN')
        used_space += 42

      if width > used_space + 28 and CONFIG['features.connection.showColumn.destination']:
        # show destination ip/port/locale (column width: 28 characters)
        etc += '%-26s  ' % destination_address
        used_space += 28

    return ('%%-%is' % width) % etc

  def _get_listing_content(self, width, listing_type):
    """
    Provides the source, destination, and extra info for our listing.

    Arguments:
      width       - maximum length of the line
      listing_type - primary attribute we're listing connections by
    """

    controller = tor_controller()
    my_type = self._entry.get_type()
    destination_address = self.get_destination_label(26, include_locale = True)

    # The required widths are the sum of the following:
    # - room for LABEL_FORMAT and LABEL_MIN_PADDING (11 characters)
    # - base data for the listing
    # - that extra field plus any previous

    used_space = len(LABEL_FORMAT % tuple([''] * 4)) + LABEL_MIN_PADDING
    local_port = ':%s' % self.connection.local_port if self.include_port else ''

    src, dst, etc = '', '', ''

    if listing_type == nyx.connection_panel.Listing.IP_ADDRESS:
      my_external_address = controller.get_info('address', self.connection.local_address)
      address_differ = my_external_address != self.connection.local_address

      # Expanding doesn't make sense, if the connection isn't actually
      # going through Tor's external IP address. As there isn't a known
      # method for checking if it is, we're checking the type instead.
      #
      # This isn't entirely correct. It might be a better idea to check if
      # the source and destination addresses are both private, but that might
      # not be perfectly reliable either.

      is_expansion_type = my_type not in (Category.SOCKS, Category.HIDDEN, Category.CONTROL)

      if is_expansion_type:
        src_address = my_external_address + local_port
      else:
        src_address = self.connection.local_address + local_port

      if my_type in (Category.SOCKS, Category.CONTROL):
        # Like inbound connections these need their source and destination to
        # be swapped. However, this only applies when listing by IP (their
        # fingerprint and nickname are both for us). Reversing the fields here
        # to keep the same column alignments.

        src = '%-21s' % destination_address
        dst = '%-26s' % src_address
      else:
        src = '%-21s' % src_address  # ip:port = max of 21 characters
        dst = '%-26s' % destination_address  # ip:port (xx) = max of 26 characters

      used_space += len(src) + len(dst)  # base data requires 47 characters

      # Showing the fingerprint (which has the width of 42) has priority over
      # an expanded address field. Hence check if we either have space for
      # both or wouldn't be showing the fingerprint regardless.

      is_expanded_address_visible = width > used_space + 28

      if is_expanded_address_visible and CONFIG['features.connection.showColumn.fingerprint']:
        is_expanded_address_visible = width < used_space + 42 or width > used_space + 70

      if address_differ and is_expansion_type and is_expanded_address_visible and self.include_expanded_addresses and CONFIG['features.connection.showColumn.expandedIp']:
        # include the internal address in the src (extra 28 characters)

        internal_address = self.connection.local_address + local_port

        # If this is an inbound connection then reverse ordering so it's:
        # <foreign> --> <external> --> <internal>
        # when the src and dst are swapped later

        if my_type == Category.INBOUND:
          src = '%-21s  -->  %s' % (src, internal_address)
        else:
          src = '%-21s  -->  %s' % (internal_address, src)

        used_space += 28

      etc = self.get_etc_content(width - used_space, listing_type)
      used_space += len(etc)
    elif listing_type == nyx.connection_panel.Listing.FINGERPRINT:
      src = 'localhost'
      dst = '%-40s' % ('localhost' if my_type == Category.CONTROL else self.get_fingerprint('UNKNOWN'))

      used_space += len(src) + len(dst)  # base data requires 49 characters

      etc = self.get_etc_content(width - used_space, listing_type)
      used_space += len(etc)
    else:
      # base data requires 50 min characters
      src = controller.get_conf('nickname', 'UNKNOWN')
      dst = controller.get_conf('nickname', 'UNKNOWN') if my_type == Category.CONTROL else self.get_nickname('UNKNOWN')

      min_base_space = 50

      etc = self.get_etc_content(width - used_space - min_base_space, listing_type)
      used_space += len(etc)

      base_space = width - used_space
      used_space = width  # prevents padding at the end

      if len(src) + len(dst) > base_space:
        src = str_tools.crop(src, base_space / 3)
        dst = str_tools.crop(dst, base_space - len(src))

      # pads dst entry to its max space

      dst = ('%%-%is' % (base_space - len(src))) % dst

    if my_type == Category.INBOUND:
      src, dst = dst, src

    padding = ' ' * (width - used_space + LABEL_MIN_PADDING)

    return LABEL_FORMAT % (src, dst, etc, padding)

  def _get_detail_content(self, width):
    """
    Provides a list with detailed information for this connection.

    Arguments:
      width - max length of lines
    """

    lines = [''] * 7
    lines[0] = 'address: %s' % self.get_destination_label(width - 11)
    lines[1] = 'locale: %s' % ('??' if self._entry.is_private() else self.get_locale('??'))

    # Remaining data concerns the consensus results, with three possible cases:
    # - if there's a single match then display its details
    # - if there's multiple potential relays then list all of the combinations
    #   of ORPorts / Fingerprints
    # - if no consensus data is available then say so (probably a client or
    #   exit connection)

    fingerprint = self.get_fingerprint()
    controller = tor_controller()

    if fingerprint:
      lines[1] = '%-13sfingerprint: %s' % (lines[1], fingerprint)  # append fingerprint to second line

      router_status_entry = controller.get_network_status(fingerprint, None)
      server_descriptor = controller.get_server_descriptor(fingerprint, None)

      if router_status_entry:
        dir_port_label = 'dirport: %s' % router_status_entry.dir_port if router_status_entry.dir_port else ''
        lines[2] = 'nickname: %-25s orport: %-10s %s' % (router_status_entry.nickname, router_status_entry.or_port, dir_port_label)
        lines[3] = 'published: %s' % router_status_entry.published.strftime("%H:%M %m/%d/%Y")
        lines[4] = 'flags: %s' % ', '.join(router_status_entry.flags)

      if server_descriptor:
        policy_label = server_descriptor.exit_policy.summary() if server_descriptor.exit_policy else 'unknown'
        lines[5] = 'exit policy: %s' % policy_label
        lines[3] = '%-35s os: %-14s version: %s' % (lines[3], server_descriptor.operating_system, server_descriptor.tor_version)

        if server_descriptor.contact:
          lines[6] = 'contact: %s' % server_descriptor.contact
    else:
      all_matches = nyx.util.tracker.get_consensus_tracker().get_all_relay_fingerprints(self.connection.remote_address)

      if all_matches:
        # multiple matches
        lines[2] = 'Multiple matches, possible fingerprints are:'

        for i in range(len(all_matches)):
          is_last_line = i == 3

          relay_port, relay_fingerprint = all_matches[i]
          line_text = '%i. or port: %-5s fingerprint: %s' % (i + 1, relay_port, relay_fingerprint)

          # if there's multiple lines remaining at the end then give a count

          remaining_relays = len(all_matches) - i

          if is_last_line and remaining_relays > 1:
            line_text = '... %i more' % remaining_relays

          lines[3 + i] = line_text

          if is_last_line:
            break
      else:
        # no consensus entry for this ip address
        lines[2] = 'No consensus data found'

    # crops any lines that are too long

    for i in range(len(lines)):
      lines[i] = str_tools.crop(lines[i], width - 2)

    return lines

  def get_destination_label(self, max_length, include_locale = False):
    """
    Provides a short description of the destination. This is made up of two
    components, the base <ip addr>:<port> and an extra piece of information in
    parentheses. The IP address is scrubbed from private connections.

    Extra information is...
    - the port's purpose for exit connections
    - the locale, the address isn't private and isn't on the local network
    - nothing otherwise

    Arguments:
      max_length       - maximum length of the string returned
      include_locale   - possibly includes the locale
    """

    # the port and port derived data can be hidden by config or without include_port

    include_port = self.include_port and (CONFIG['features.connection.showExitPort'] or self._entry.get_type() != Category.EXIT)

    # destination of the connection

    address_label = '<scrubbed>' if self._entry.is_private() else self.connection.remote_address
    port_label = ':%s' % self.connection.remote_port if include_port else ''
    destination_address = address_label + port_label

    # Only append the extra info if there's at least a couple characters of
    # space (this is what's needed for the country codes).

    if len(destination_address) + 5 <= max_length:
      space_available = max_length - len(destination_address) - 3

      if self._entry.get_type() == Category.EXIT and include_port:
        purpose = connection.port_usage(self.connection.remote_port)

        if purpose:
          # BitTorrent is a common protocol to truncate, so just use "Torrent"
          # if there's not enough room.

          if len(purpose) > space_available and purpose == 'BitTorrent':
            purpose = 'Torrent'

          # crops with a hyphen if too long

          purpose = str_tools.crop(purpose, space_available, ending = str_tools.Ending.HYPHEN)

          destination_address += ' (%s)' % purpose
      elif not connection.is_private_address(self.connection.remote_address):
        extra_info = []

        if include_locale and not tor_controller().is_geoip_unavailable():
          foreign_locale = self.get_locale('??')
          extra_info.append(foreign_locale)
          space_available -= len(foreign_locale) + 2

        if extra_info:
          destination_address += ' (%s)' % ', '.join(extra_info)

    return destination_address[:max_length]
