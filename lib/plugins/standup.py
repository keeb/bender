import time
import archives

class Standup(object):
    def __init__(self, name, irc, server, global_config, config):
        self._name = name
        self._archives = archives.DiskArchives(global_config, config)
        self._irc = irc
        self._server = server
        self._global_config = global_config
        self._config = config
        self._in_progress = False
        self._starting = False
        self._owner = None
        self._started = None
        self._parking = None
        self._user_late_list = None
        self._user_list = None
        self._current_user = None
        self._action_voting = False
        self._vote_count = 0
        self._nicks_voted = None
        self._topic_contributors = None
        self._interrupted = False
        self._action_items = None
        self._topic_list = None


    def _register_handlers(self):
        self._irc.add_global_handler('pubmsg', self._event_pubmsg)

    def _event_pubmsg(self, conn, event):
        args = event.arguments
        if not args:
            return
        if self._in_progress is True and event.target == self._config['standup_channel']:
            # Archiving
            nick = event.source.split('!')[0].lower()
            self._archives.write('{0}: {1}'.format(nick, args[0]))
        if args[0].startswith(self._global_config['nick']):
            self._direct_message(event)

    def _direct_message(self, event):
        target = event.target
        args = [arg for arg in event.arguments[0].split(' ') if arg]
        nick = event.source.split('!')[0].lower()
        args.pop(0)
        if not args:
            return
        f_cmd = '_cmd_' + args[0].lower()
        if hasattr(self, f_cmd):
            args.pop(0)
            getattr(self, f_cmd)(target, nick, args)

    def _cmd_help(self, target, nick, args):
        """ Display the help menu """
        options = {}
        for meth in dir(self):
            if not meth.startswith('_cmd_'):
                continue
            cmeth = getattr(self, meth)
            doc = cmeth.__doc__.split('\n')[0].strip() if cmeth.__doc__ else '<undocumented>'
            options[meth[5:]] = doc
        if not args:
            self._send_msg(target, nick, ('My commands are: {0}. Ask me '
                '"help <command>" for what they do.').format(', '.join(options.keys())))
            return
        cmd = args[0].lower()
        if cmd in options:
            self._send_msg(target, nick, options[cmd])
            return
        self._send_msg(target, nick, 'WTF?! Try "help"')

    def _topic_contributor(self, conn, event):
        nick = event.source.split('!')[0].lower()
        if nick == self._current_user:
            return
        if nick in self._topic_contributors:
            return
        self._topic_contributors.append(nick)

    def _cmd_start(self, target, nick, args):
        """ start: start a standup

        1/ all users on the standup channel are asked to say something
        2/ all replies are gathered for 1 min
        3/ starts the standup with the users who replied
        """
        if self._starting is True or self._in_progress is True:
            self._send_msg(target, nick, 'Cannot start a standup twice.')
            return

        self._owner = nick
        self._server.privmsg(self._config['standup_channel'],
                'Starting the weekly standup "{0}" on {1}'.format(self._name, self._config['standup_channel']))
        self._starting = True
        nick_list = []

        def list_users(conn, event):
            self._irc.remove_global_handler('namreply', list_users)
            users = event.arguments.pop().split(' ')
            users.pop(0)
            if self._global_config['nick'] in users:
                users.remove(self._global_config['nick'])
            users = map(lambda c: c.lstrip('@+'), users)
            self._server.privmsg(self._config['standup_channel'],
                    '{0}: Please say something to be part of the standup (starting in {1} seconds)'.format(
                        ', '.join(users), self._config['warmup_duration']))


        def gather_reply(conn, event):
            if self._starting is False:
                return
            if event.target != self._config['standup_channel']:
                return
            nick = event.source.split('!')[0].lower()
            if nick not in nick_list:
                nick_list.append(nick)


        def start():
            self._starting = False
            self._in_progress = True
            self._interrupted = False
            self._started = time.time()
            self._topic_list = {}
            # Stop gathering
            self._irc.remove_global_handler('pubmsg', gather_reply)
            if not nick_list:
                self._server.privmsg(self._config['standup_channel'],
                        'Nobody replied, aborting the standup.')
                return
            self._archives.new(self._name)
            self._user_late_list = []
            self._parking = []
            self._action_items = {}
            self._server.privmsg(self._config['standup_channel'],
                    'Let\'s start the standup with {0}'.format(', '.join(nick_list)))
            self._archives.write('*** Starting with: {0}'.format(', '.join(nick_list)))
            self._user_list = nick_list
            self._current_user = nick_list[0]
            self._topic_contributors = []
            self._send_msg(self._config['standup_channel'], self._current_user,
                    'You start.')
            self._irc.add_global_handler('pubmsg', self._topic_contributor)
            self._archives.write('*** Current: {0}'.format(self._current_user))

        self._irc.add_global_handler('namreply', list_users)
        self._irc.add_global_handler('pubmsg', gather_reply)
        self._server.names([self._config['standup_channel']])
        self._irc.execute_at(int(time.time() + self._config['warmup_duration']), start)


    def _cmd_topic(self, target, nick, args):
        topic = "-".join(args).replace('/', '-')

        self._send_msg(target, nick, 'Got it')
        users_topics = self._topic_list.get(nick)
        if users_topics:
            self._topic_list[nick].append(topic)
        else:
            self._topic_list[nick] = [topic]

        _archives = archives.DiskArchives(self._global_config, self._config)
        _archives.new('#{0}'.format(topic))

        def log_line(conn, event):
            nick = event.source.split('!')[0].lower()
            said = "".join(event.arguments)
            _archives.write("".join([nick, "||", said]))

        self._irc.add_global_handler('pubmsg', log_line)


    def _cmd_action(self, target, nick, args):
        if self._action_voting:
            self._send_msg(target, nick, 'vote is already in progress')
            return

        self._nicks_voted = []

        def gather_reply(conn, event):
            if not self._action_voting:
                return
            nick = event.source.split('!')[0].lower()
            said = event.arguments
            if "+1" in said:
                if nick in self._nicks_voted:
                    self._server.privmsg(self._config['standup_channel'], "{0} has already voted".format(nick))
                else:
                    self._vote_count += 1
                    self._nicks_voted.append(nick)
            if "-1" in said:
                if nick in self._nicks_voted:
                    self._server.privmsg(self._config['standup_channel'], "{0} has already voted".format(nick))
                else:
                    self._vote_count -= 1
                    self._nicks_voted.append(nick)

        self._vote_count = 0
        self._action_voting = True
        self._server.privmsg(self._config['standup_channel'], 'action item acknowledged. opening for voting for the next 1 minute')
        self._irc.add_global_handler('pubmsg', gather_reply)

        def vote_end():
            self._action_voting = False
            self._server.privmsg(self._config['standup_channel'], "voting has ended. {0} people voted with a total score of {1}".format(len(self._nicks_voted), self._vote_count))

        self._irc.execute_at(int(time.time() + self._config['vote_duration']), vote_end)



    def _cmd_add(self, target, nick, args):
        """ Add a person to the standup (I won't check if the nick exists on the server) """
        if not args:
            return
        if self._in_progress is False:
            self._send_msg(target, nick, 'No standup in progress.')
            return
        to_add = args[0].lower()
        if to_add == 'me':
            to_add = nick
        if nick and self._owner != nick and to_add != nick:
            self._send_msg(target, nick, 'Only {0} can add someone (he started the standup).'.format(self._owner))
            return
        if to_add in self._user_list:
            self._send_msg(target, nick, '{0} is already part of the Standup.'.format(to_add))
            return
        # FIXME: Check if to_add exists for real
        self._user_list.append(to_add)
        self._user_late_list.append(to_add)
        if to_add == nick:
            self._send_msg(target, nick, 'You\'re in.')
            return
        self._send_msg(target, nick, 'Added {0}.'.format(to_add))

    def _cmd_next(self, target=None, nick=None, args=None):
        self._interrupted = False

        def interrupt_next(conn, event):
            nick = event.source.split('!')[0].lower()
            if nick in self._topic_contributors:
                self._server.privmsg(self._config['standup_channel'], "Interrupted by {0}. {1} please call next again after this conflict has been resolved.".format(nick, self._current_user))
                self._interrupted = True
                self._irc.remove_global_handler('pubmsg', interrupt_next)

        def real_next():
            if self._interrupted:
                return

            self._user_list.pop(0)
            if not self._user_list:
                self._cmd_stop()
                return
            self._current_user = self._user_list[0]
            self._send_msg(self._config['standup_channel'], self._current_user,
                    'You\'re next.')
            self._topic_contributors = []
            self._archives.write('*** Current: {0}'.format(self._current_user))
            self._irc.remove_global_handler('pubmsg', interrupt_next)
            
        """ next: when you are done talking """
        if self._in_progress is False:
            self._send_msg(target, nick, 'No standup in progress.')
            return

        if nick and nick != self._current_user:
            self._send_msg(target, nick, 'Only {0} can say "next".'.format(self._current_user))
            return

        if len(self._topic_contributors) > 0:
            self._server.privmsg(self._config['standup_channel'], "cc {0}".format(" ".join(self._topic_contributors)))
            self._server.privmsg(self._config['standup_channel'], "Unless interrupted in the next 20 seconds,  we'll move on to the next speaker")
            self._irc.add_global_handler('pubmsg', interrupt_next)
        else:
            real_next()

        self._irc.execute_at(int(time.time() + 20), real_next)

    def _cmd_skip(self, target, nick, args):
        """ skip <nick>: skip a person """
        if self._in_progress is False:
            self._send_msg(target, nick, 'No standup in progress.')
            return
        if target != self._config['standup_channel']:
            # Wrong channel, ignoring
            return
        if self._owner and nick and self._owner != nick:
            self._send_msg(target, nick, 'Only {0} can skip someone (he started the standup).'.format(self._owner))
            return
        if not args:
            return
        to_skip = args[0].lower()
        if to_skip == self._current_user:
            self._cmd_next()
            return
        if to_skip not in self._user_list:
            return
        self._user_list.remove(to_skip)
        self._send_msg(target, nick, '{0} has been removed from the standup.'.format(to_skip))

    def _cmd_park(self, target, nick, args):
        """ park <topic>: park a topic for later """
        if self._in_progress is False:
            self._send_msg(target, nick, 'No standup in progress.')
            return
        self._parking.append('({0}) {1}'.format(nick, ' '.join(args)))
        self._send_msg(target, nick, 'Parked.')

    def _cmd_ping(self, target, nick, args):
        self._send_msg(target, nick, 'pong')

    def _cmd_eat(self, target, nick, args):
        self._send_msg(target, nick, 'CACTUS! OM NOM NOM NOM')

    def _cmd_love(self, target, nick, args):
        self._send_msg(target, nick, 'I love you too <3')

    def _cmd_stop(self, target=None, nick=None, args=None):
        """ stop: stop a standup """
        if self._in_progress is False:
            self._send_msg(target, nick, 'No standup in progress.')
            return
        if self._owner and nick and self._owner != nick:
            self._send_msg(target, nick, 'Only {0} can stop the standup (he started it).'.format(self._owner))
            return

        self._user_list = None
        self._current_user = None
        self._in_progress = False
        elapsed = int((time.time() - self._started) / 60)
        self._started = None
        self._server.privmsg(self._config['standup_channel'],
                'All done! Standup was {0} minutes.'.format(elapsed))
        user_late_list = ', '.join(self._user_late_list)
        self._archives.write('*** Standup was {0} minutes'.format(elapsed))

        if self._parking:
            self._archives.write('Parked topics: ')
            self._server.privmsg(self._config['standup_channel'], 'Parked topics from "{0}" standup:'.format(self._name))
            send = lambda m: self._server.privmsg(self._config['standup_channel'], m)
            for park in self._parking:
                send('* ' + park)
                self._archives.write('* ' + park)

        if self._topic_list:
            for k,v in self._topic_list.iteritems():
                self._server.privmsg(self._config['standup_channel'], k)
                for i in v:
                    self._server.privmsg(self._config['standup_channel'], '\t - {0}'.format(i))

        self._archives.close()
        self._parking = False

    def _send_msg(self, target, nick, msg):
        """ Send a message to a nick and target
        Each message sent is prefixed by the nick name (use to talk to someone)
        """
        if hasattr(msg, '__iter__'):
            for m in msg:
                self._server.privmsg(target, '{0}: {1}'.format(nick, m))
            return
        self._server.privmsg(target, '{0}: {1}'.format(nick, msg))

    def run(self):
        self._register_handlers()
        self._server.join(self._config['dev_channel'])
        self._server.join(self._config['standup_channel'])
