import discord
from discord.ext import commands
import wavelink
import json
import datetime
import asyncio
import random

# Load config
with open("config.json") as f:
    config = json.load(f)

BOT_TOKEN = config["BOT_TOKEN"]
LAVALINK_HOST = config.get("LAVALINK_HOST", "localhost")
LAVALINK_PORT = config.get("LAVALINK_PORT", 2333)
LAVALINK_PASSWORD = config.get("LAVALINK_PASSWORD", "youshallnotpass")

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

# Initialize bot with command_prefix=None
bot = commands.Bot(command_prefix=None, intents=intents)

# Override on_message to prevent traditional command processing
@bot.event
async def on_message(message: discord.Message):
    # This prevents discord.ext.commands from trying to process traditional prefixes.
    # We only care about slash commands, which are handled by bot.tree.
    if message.author.bot:
        return # Ignore messages from other bots


# Custom wavelink Player to manage queue and history
class CustomPlayer(wavelink.Player):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._custom_queue = asyncio.Queue()  # Our custom queue for advanced management
        self.history = []
        self.loop_mode = 0  # 0: Off, 1: Song, 2: Queue
        self.current_message = None # To update the now playing embed

    async def add_to_custom_queue(self, track: wavelink.Playable):
        await self._custom_queue.put(track)

    async def play_next_track_from_custom_queue(self):
        # This function is now specifically for playing the "next" song
        # from our custom queue, after the current one ends or is skipped.

        if self.loop_mode == 1 and self.current: # Loop current song
            await self.play(self.current) # Replay the current track
            await self.update_now_playing_message()
            return

        if self._custom_queue.empty():
            if self.loop_mode == 2 and self.history: # Loop queue
                # Add history back to custom queue
                for track in self.history:
                    await self._custom_queue.put(track)
                self.history.clear()
            else:
                # No more tracks in custom queue or history, disconnect
                await self.disconnect_and_clean_up()
                return

        next_track = await self._custom_queue.get()
        # Add the *just finished* track to history
        if self.current: # self.current would be the track that just finished or was stopped
            self.history.append(self.current)
            if len(self.history) > 10: # Keep history manageable
                self.history.pop(0)

        await self.play(next_track) # Play the next track using wavelink's play
        await self.update_now_playing_message()


    async def update_now_playing_message(self):
        current_track = self.current
        if not self.current_message or not current_track:
            return

        embed = discord.Embed(
            title="<a:musicaaa:1374994485066469386> Now Playing",
            color=discord.Color.blue()
        )
        embed.add_field(name="Song", value=f"**{current_track.title}**", inline=False)
        duration_seconds = current_track.length / 1000
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        embed.add_field(name="<a:durdation:1374998011020840980> Duration", value=f"{minutes}:{seconds:02d}", inline=False)
        embed.add_field(name="Artist", value=current_track.author if current_track.author else "Unknown Artist", inline=False)
        embed.add_field(name="<:dsdmember:1374997619935281283> Requested by", value="Bot (Queue)", inline=False) # Placeholder
        embed.add_field(name="<a:welcomeada:1374997616844341359> Requested at", value=datetime.datetime.now().strftime("%H:%M:%S"), inline=False)
        embed.set_thumbnail(url=current_track.artwork)
        embed.set_footer(text=f"{bot.user.name} | Enjoy your time!")

        try:
            await self.current_message.edit(embed=embed, view=MusicControls())
        except discord.NotFound:
            self.current_message = None
        except Exception as e:
            print(f"Error updating now playing message: {e}")

    async def disconnect_and_clean_up(self):
        self._custom_queue = asyncio.Queue()
        self.history = []
        self.loop_mode = 0
        if self.current_message:
            try:
                await self.current_message.edit(embed=None, view=None, content="Queue finished. Disconnected from voice.")
            except discord.NotFound:
                pass
            self.current_message = None
        await self.disconnect()


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    node = wavelink.Node(uri=f"http://{LAVALINK_HOST}:{LAVALINK_PORT}", password=LAVALINK_PASSWORD)
    await wavelink.Pool.connect(client=bot, nodes=[node])

    print("Connected to Lavalink")

    try:
        await bot.tree.sync()
        print("Slash commands synced.")
    except Exception as e:
        print(f"Error syncing commands: {e}")

@bot.event
async def on_wavelink_track_end(payload: wavelink.TrackEndEventPayload):
    player: CustomPlayer = payload.player
    # Ensure a player exists and it's the expected CustomPlayer
    if not player or not isinstance(player, CustomPlayer):
        return

    # Using integer values for reason codes for broader Wavelink version compatibility
    # 0 typically corresponds to FINISHED, 1 to STOPPED in older Wavelink versions
    if payload.reason == 0:  # FINISHED
        # A track finished naturally, now play the next one from our queue
        await player.play_next_track_from_custom_queue()
    elif payload.reason == 1: # STOPPED
        # Track was stopped (e.g., by skip or stop command), immediately attempt to play next
        if not player._custom_queue.empty():
            await player.play_next_track_from_custom_queue()
        else:
            await player.disconnect_and_clean_up()


@bot.tree.command(name="join", description="Joins your voice channel.")
async def join(interaction: discord.Interaction):
    if not interaction.user.voice:
        await interaction.response.send_message("You're not in a voice channel!", ephemeral=True)
        return

    channel = interaction.user.voice.channel
    await channel.connect(cls=CustomPlayer)
    await interaction.response.send_message(f"Joined {channel.mention}")


class MusicControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="<:11previous:1375009213893447761>")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("I'm not in a voice channel.")
            return

        if vc.history:
            previous_track = vc.history.pop()
            if vc.current:
                # Put the current track back to the *front* of the custom queue
                temp_queue = asyncio.Queue()
                await temp_queue.put(vc.current)
                while not vc._custom_queue.empty():
                    await temp_queue.put(await vc._custom_queue.get())
                vc._custom_queue = temp_queue

            await vc.play(previous_track)
            await interaction.followup.send(f"Playing previous song: **{previous_track.title}**")
            await vc.update_now_playing_message()
        else:
            await interaction.followup.send("No previous song in history.")

    @discord.ui.button(label="Play", style=discord.ButtonStyle.green, emoji="<:1playbutton:1375012787595776010>")
    async def play_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if vc and vc.paused:
            await vc.pause(False) # Explicitly set to resume
            await interaction.followup.send("Resumed music!")
        else:
            await interaction.followup.send("Music is already playing or no music to resume.")

    @discord.ui.button(label="Pause", style=discord.ButtonStyle.secondary, emoji="<:111pause:1375012784839987254>")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if vc and vc.playing:
            await vc.pause(True) # Explicitly set to pause
            await interaction.followup.send("Paused music!")
        else:
            await interaction.followup.send("No music playing to pause.")

    @discord.ui.button(label="Skip", style=discord.ButtonStyle.secondary, emoji="<:1skipbutton:1375012780641488936>")
    async def skip_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("I'm not in a voice channel.")
            return

        if not vc.current and vc._custom_queue.empty() and not vc.loop_mode == 2:
            await interaction.followup.send("No more songs in the queue to skip to.")
            return

        if vc.playing or vc.paused: # Can skip even if paused
            await vc.stop() # This will trigger on_wavelink_track_end
            await interaction.followup.send("Skipped song!")
        else:
            await interaction.followup.send("No music playing to skip.")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="<:1stop:1375012777642430544>")
    async def stop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if vc:
            await vc.disconnect_and_clean_up()
            await interaction.followup.send("Disconnected from voice and cleared queue.")
        else:
            await interaction.followup.send("I'm not in a voice channel.")

    @discord.ui.button(label="Queue", style=discord.ButtonStyle.secondary, emoji="<:1queue:1375009211255357480>", row=1)
    async def queue_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if not vc or (vc.current is None and vc._custom_queue.empty()): # Check for current and custom queue
            await interaction.followup.send("The queue is empty.")
            return

        queue_list = []
        if vc.current:
            queue_list.append(f"**Now Playing:** **{vc.current.title}** by {vc.current.author}")

        # Temporarily get items from queue to display, then put them back
        temp_queue_items = []
        while not vc._custom_queue.empty():
            item = await vc._custom_queue.get()
            queue_list.append(f"**{item.title}** by {item.author}")
            temp_queue_items.append(item)

        # Put items back into the queue
        for item in temp_queue_items:
            await vc._custom_queue.put(item)

        if not queue_list: # Should not happen if current or custom_queue was not empty
            await interaction.followup.send("The queue is empty.")
            return

        queue_display = "\n".join([f"{i+1}. {song}" for i, song in enumerate(queue_list[:10])]) # Show top 10
        if len(queue_list) > 10:
            queue_display += f"\n...and {len(queue_list) - 10} more."

        embed = discord.Embed(title="Music Queue", description=queue_display, color=discord.Color.purple())
        await interaction.followup.send(embed=embed)


    @discord.ui.button(label="Shuffle", style=discord.ButtonStyle.secondary, emoji="<:1autoplay:1375009203508215839>", row=1)
    async def shuffle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("I'm not in a voice channel.")
            return

        if vc._custom_queue.empty():
            await interaction.followup.send("Queue is empty, nothing to shuffle.")
            return

        queue_items = []
        while not vc._custom_queue.empty():
            queue_items.append(await vc._custom_queue.get())

        random.shuffle(queue_items)

        for item in queue_items:
            await vc._custom_queue.put(item)

        await interaction.followup.send("Queue shuffled!")

    @discord.ui.button(label="Loop", style=discord.ButtonStyle.secondary, emoji="<:1refresh:1375012783116128276>", row=1)
    async def loop_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("I'm not in a voice channel.")
            return

        vc.loop_mode = (vc.loop_mode + 1) % 3 # Cycle through 0, 1, 2
        modes = {0: "Off", 1: "Song", 2: "Queue"}
        await interaction.followup.send(f"Loop mode set to: **{modes[vc.loop_mode]}**")

    @discord.ui.button(label="Vol +", style=discord.ButtonStyle.secondary, emoji="<:1mediumvolume:1375014100257603634>", row=1)
    async def vol_up_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("I'm not in a voice channel.")
            return

        current_volume = vc.volume # Volume is 0-1000
        new_volume = min(1000, current_volume + 100) # Increase by 100, max 1000
        await vc.set_volume(new_volume)
        await interaction.followup.send(f"Volume set to {new_volume // 10}%")

    @discord.ui.button(label="Vol -", style=discord.ButtonStyle.secondary, emoji="<:1lowvolume:1375014097611001896>", row=1)
    async def vol_down_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        vc: CustomPlayer = interaction.guild.voice_client
        if not vc:
            await interaction.followup.send("I'm not in a voice channel.")
            return

        current_volume = vc.volume
        new_volume = max(0, current_volume - 100) # Decrease by 100, min 0
        await vc.set_volume(new_volume)
        await interaction.followup.send(f"Volume set to {new_volume // 10}%")


@bot.tree.command(name="play", description="Plays a song from YouTube.")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()

    vc: CustomPlayer = interaction.guild.voice_client
    if not vc:
        if not interaction.user.voice:
            await interaction.followup.send("You're not in a voice channel!", ephemeral=True)
            return
        vc = await interaction.user.voice.channel.connect(cls=CustomPlayer)

    tracks = await wavelink.Playable.search(query)
    if not tracks:
        await interaction.followup.send(f"No tracks found for '{query}'")
        return

    track = tracks[0]

    # THIS IS THE CRUCIAL LOGIC FOR QUEUEING SONGS
    if vc.playing:
        # If something is already playing, add to our custom queue
        await vc.add_to_custom_queue(track)
        await interaction.followup.send(f"Added **{track.title}** to the queue.")
    else:
        # If nothing is playing, play this track directly
        await vc.play(track)
        
        embed = discord.Embed(
            title="<a:musicaaa:1374994485066469386> Now Playing",
            color=discord.Color.blue()
        )
        embed.add_field(name="Song", value=f"**{track.title}**", inline=False)
        duration_seconds = track.length / 1000
        minutes = int(duration_seconds // 60)
        seconds = int(duration_seconds % 60)
        embed.add_field(name="<a:durdation:1374998011020840980> Duration", value=f"{minutes}:{seconds:02d}", inline=False)
        embed.add_field(name="Artist", value=track.author if track.author else "Unknown Artist", inline=False)
        embed.add_field(name="<:dsdmember:1374997619935281283> Requested by", value=interaction.user.mention, inline=False)
        embed.add_field(name="<a:welcomeada:1374997616844341359> Requested at", value=datetime.datetime.now().strftime("%H:%M:%S"), inline=False)
        embed.set_thumbnail(url=track.artwork)
        embed.set_footer(text=f"{bot.user.name} | Enjoy your time!")

        msg = await interaction.followup.send(embed=embed, view=MusicControls())
        vc.current_message = msg # Store the message to update later

        # After playing the first track, if there are more tracks in our custom queue,
        # we can initiate playback of the next one here. This is crucial for seamless transitions.
        if not vc._custom_queue.empty():
            # Schedule the next track to play without blocking the current command response
            bot.loop.create_task(vc.play_next_track_from_custom_queue())


# The /stop slash command has been removed as per your request.
# The 'Stop' button in MusicControls still handles disconnecting and clearing the queue.

bot.run(BOT_TOKEN)