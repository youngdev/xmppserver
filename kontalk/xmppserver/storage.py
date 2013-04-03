# -*- coding: utf-8 -*-
"""Storage modules."""
"""
  Kontalk XMPP server
  Copyright (C) 2011 Kontalk Devteam <devteam@kontalk.org>

 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, either version 3 of the License, or
 (at your option) any later version.

 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.

 You should have received a copy of the GNU General Public License
 along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""


from twisted.internet import defer
from twisted.enterprise import adbapi
from twisted.words.protocols.jabber import jid

from wokkel import generic

import os, base64, time, datetime

try:
    from collections import OrderedDict
except:
    from ordereddict import OrderedDict

import util, xmlstream2, log

dbpool = None

def init(config):
    global dbpool
    dbpool = adbapi.ConnectionPool(config['dbmodule'], host=config['host'], port=config['port'],
        user=config['user'], passwd=config['password'], db=config['dbname'], autoreconnect=True)


""" interfaces """


class StanzaStorage:
    """Stanza storage system."""

    def store(self, stanza):
        """Store a stanza."""
        pass

    def get_by_id(self, stanzaId):
        """Retrieve a stanza by id."""
        pass

    def get_by_sender(self, sender):
        """Retrieve stanzas by sender."""
        pass

    def get_by_recipient(self, recipient):
        """Retrieve stanzas by recipient."""
        pass

    def delete(self, stanzaId, sender=None, recipient=None):
        """Delete a stanza by id."""
        pass


class PresenceStorage:
    """Presence cache storage."""

    def get(self, userid, resource):
        """Retrieve info about a user."""
        pass

    def presence(self, stanza):
        """Persist a presence."""
        pass

    def touch(self, user_jid):
        """Update last seen timestamp of a user."""
        pass


class NetworkStorage:
    """Network info storage."""

    def get_list(self):
        """Retrieve the list of servers in this network."""
        pass


class UserValidationStorage:
    """User validation storage."""

    """Validation code length."""
    VALIDATION_CODE_LENGTH = 6

    def register(self, key, code=None):
        """Registers a validation code for a user."""
        pass

    def validate(self, code):
        """Check if code is valid and deletes it."""
        pass


class FileStorage:
    """File storage."""

    def init(self):
        """Initializes this storage driver."""
        pass

    def get(self, name, return_data=True):
        """Retrieves a stored file."""
        pass

    def store_file(self, name, mime, fn):
        """Stores a file reading data from a file-like object."""
        pass

    def store_data(self, name, mime, data):
        """Stores a file reading data from a string."""
        pass


""" implementations """


class MySQLStanzaStorage(StanzaStorage):

    def store(self, stanza):
        global dbpool
        receipt = xmlstream2.extract_receipt(stanza, 'request')
        if receipt:
            # this is indeed generated by server :)
            msgId = receipt['id']
        else:
            # WARNING stanza id must be server generated
            msgId = stanza['id']
        args = (
            msgId,
            util.jid_to_userid(jid.JID(stanza['from'])),
            util.jid_to_userid(jid.JID(stanza['to'])),
            stanza.toXml().encode('utf-8').decode('utf-8'),
            int(time.time()*1e3),
        )
        return dbpool.runOperation('INSERT INTO stanzas (id, sender, recipient, content, timestamp) VALUES(?, ?, ?, ?, ?)', args)

    def get_by_id(self, stanzaId):
        global dbpool
        def _translate(tx, stanzaId):
            # TODO translation to dict
            tx.execute('SELECT content, timestamp FROM stanzas WHERE id = ?', (stanzaId, ))
            return tx.fetchone()
        return dbpool.runInteraction(_translate, stanzaId)

    def get_by_sender(self, sender):
        # TODO
        #global dbpool
        #return dbpool.runQuery('SELECT id, recipient, content, timestamp FROM stanzas WHERE sender = ?', sender)
        pass

    def get_by_recipient(self, recipient):
        global dbpool
        def _translate(tx, recipient):
            tx.execute('SELECT id, timestamp, content FROM stanzas WHERE recipient = ? ORDER BY timestamp', (recipient.user, ))
            data = tx.fetchall()
            out = []
            for row in data:
                stanzaId = str(row[0])
                d = { 'id': stanzaId, 'timestamp': datetime.datetime.utcfromtimestamp(row[1] / 1e3) }
                d['stanza'] = generic.parseXml(row[2].decode('utf-8').encode('utf-8'))

                """
                Add a <storage/> element to the stanza; this way components have
                a way to know if stanza is coming from storage.
                """
                stor = d['stanza'].addElement((xmlstream2.NS_XMPP_STORAGE, 'storage'))
                stor['id'] = stanzaId

                out.append(d)
            return out
        return dbpool.runInteraction(_translate, recipient)

    def delete(self, stanzaId, sender=None, recipient=None):
        global dbpool
        #import traceback
        #log.debug("deleting stanza %s -- traceback:\n%s" % (stanzaId, ''.join(traceback.format_stack())))
        q = 'DELETE FROM stanzas WHERE id = ?'
        args = [stanzaId]
        if sender:
            q += ' AND sender LIKE ?'
            args.append(sender + '%')
        if recipient:
            q += ' AND recipient LIKE ?'
            args.append(recipient + '%')

        return dbpool.runOperation(q, args)

class MySQLNetworkStorage(NetworkStorage):

    def get_list(self):
        global dbpool
        def _translate(tx):
            out = {}
            tx.execute('SELECT fingerprint, host FROM servers')
            data = tx.fetchall()
            for row in data:
                # { fingerprint: host }
                out[str(row[0])] = str(row[1])
            return out
        return dbpool.runInteraction(_translate)

class MySQLPresenceStorage(PresenceStorage):

    def get(self, userid, resource):
        def _fetchone(tx, query, args):
            tx.execute(query, args)
            data = tx.fetchone()
            if data:
                return {
                    'userid': data[0],
                    'timestamp': data[1],
                    'status': base64.b64decode(data[2]).decode('utf-8') if data[2] is not None else '',
                    'show': data[3],
                    'priority': data[4],
                }
        def _fetchall(tx, query, args):
            tx.execute(query, args)
            data = tx.fetchall()
            out = []
            for d in data:
                out.append({
                    'userid': d[0],
                    'timestamp': d[1],
                    'status': base64.b64decode(d[2]).decode('utf-8') if d[2] is not None else '',
                    'show': d[3],
                    'priority': d[4],
                })
            return out

        if resource:
            interaction = _fetchone
            query = 'SELECT `userid`, `timestamp`, `status`, `show`, `priority` FROM presence WHERE userid = ?'
            args = (userid + resource, )
        else:
            interaction = _fetchall
            query = 'SELECT `userid`, `timestamp`, `status`, `show`, `priority` FROM presence WHERE userid LIKE ? ORDER BY `timestamp` DESC'
            args = (userid + '%', )

        return dbpool.runInteraction(interaction, query, args)

    def presence(self, stanza):
        global dbpool
        sender = jid.JID(stanza['from'])
        userid = util.jid_to_userid(sender)

        def encode_not_empty(val):
            if val is not None:
                data = val.__str__().encode('utf-8')
                if len(data) > 0:
                    return base64.b64encode(val.__str__().encode('utf-8'))
            return None

        try:
            priority = int(stanza.priority.__str__())
        except:
            priority = 0

        values = (userid, encode_not_empty(stanza.status), util.str_none(stanza.show), priority)
        return dbpool.runOperation('REPLACE INTO presence VALUES(?, UTC_TIMESTAMP(), ?, ?, ?)', values)

    def touch(self, user_jid):
        global dbpool
        userid = util.jid_to_userid(user_jid)
        return dbpool.runOperation('UPDATE presence SET timestamp = UTC_TIMESTAMP() WHERE userid = ?', (userid, ))


class MySQLUserValidationStorage(UserValidationStorage):
    """User validation storage."""

    TEXT_INVALID_CODE = 'Invalid validation code.'

    def register(self, key, code=None):
        global dbpool

        if not code:
            code = util.rand_str(self.VALIDATION_CODE_LENGTH, util.CHARSBOX_NUMBERS)

        def _callback(result, callback, code):
            callback.callback(code)
        def _errback(failure, callback):
            callback.errback(failure)

        callback = defer.Deferred()
        d = dbpool.runOperation('INSERT INTO validations VALUES (?, ?, sysdate())', (key, code, ))
        d.addCallback(_callback, callback, code)
        d.addErrback(_errback, callback)
        return callback

    def validate(self, code):
        global dbpool

        if len(code) != self.VALIDATION_CODE_LENGTH or not code.isdigit():
            return defer.fail(RuntimeError(self.TEXT_INVALID_CODE))

        def _fetch(tx, code):
            tx.execute('SELECT userid FROM validations WHERE code = ?', (code, ))
            data = tx.fetchone()
            if data:
                # delete code immediately
                tx.execute('DELETE FROM validations WHERE code = ?', (code, ))
                return data[0]
            else:
                raise RuntimeError(self.TEXT_INVALID_CODE)

        return dbpool.runInteraction(_fetch, code)


class DiskFileStorage(FileStorage):
    """File storage."""

    def __init__(self, path):
        self.path = path

    def init(self):
        try:
            os.makedirs(self.path)
        except:
            pass

    def get(self, name, return_data=True):
        if return_data:
            # TODO
            raise NotImplementedError()
        else:
            fn = os.path.join(self.path, name)
            if os.path.isfile(fn):
                return fn

    def store_file(self, name, mime, fn):
        # TODO
        pass

    def store_data(self, name, mime, data):
        filename = os.path.join(self.path, name)
        f = open(filename, 'w')
        f.write(data)
        f.close()
        return filename
